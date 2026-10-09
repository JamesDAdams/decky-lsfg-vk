import json
import os
import shutil
import tarfile
import tempfile
import traceback
from pathlib import Path
from typing import Dict

from .base_service import BaseService
from .config_schema import ConfigurationManager, ProfileData, UnsupportedConfigurationVersion
from .constants import (
    ARCHIVE_FILENAME,
    ARM_ARCHIVE_FILENAME,
    ARM_LIB_SOURCE_FILENAME,
    ARM_MANIFEST_FILENAME,
    BIN_DIR,
    CLI_FILENAME,
    JSON_FILENAME,
    JSON_X86_FILENAME,
    LEGACY_JSON_FILENAME,
    LEGACY_LIB_FILENAME,
    LIB_FILENAME,
    LIB_X86_FILENAME,
    LOCAL_SHARE,
    UI_DESKTOP_FILENAME,
    UI_FILENAME,
    UI_ICON_FILENAME,
)
from .runtime_service import RuntimeService
from .steam_service import SteamService
from .types import InstallationCheckResponse, InstallationResponse, UninstallationResponse


class InstallationService(BaseService):
    def __init__(
        self,
        logger=None,
        runtime_service: RuntimeService = None,
        steam_service: SteamService = None,
    ):
        super().__init__(logger)
        self.runtime_service = runtime_service or RuntimeService(logger=self.log)
        self.steam_service = steam_service or SteamService(logger=self.log)
        self.lib_file = self.local_lib_dir / LIB_FILENAME
        self.lib_x86_file = self.local_lib_dir / LIB_X86_FILENAME
        self.json_file = self.local_share_dir / JSON_FILENAME
        self.json_x86_file = self.local_share_dir / JSON_X86_FILENAME
        self.cli_file = self.local_bin_dir / CLI_FILENAME
        self.legacy_lib_file = self.local_lib_dir / LEGACY_LIB_FILENAME
        self.legacy_json_file = self.local_share_dir / LEGACY_JSON_FILENAME
        self.arm_archive_path = Path(__file__).parent.parent.parent / BIN_DIR / ARM_ARCHIVE_FILENAME

    def install(self) -> InstallationResponse:
        try:
            archive_path = Path(__file__).parent.parent.parent / BIN_DIR / ARCHIVE_FILENAME
            if not archive_path.exists():
                raise FileNotFoundError(f"{ARCHIVE_FILENAME} not found at {archive_path}")
            self._ensure_directories()
            profile_data = self._prepare_config()
            self._remove_legacy_layer_files()
            self._install_archive(archive_path)
            if self.is_arm_host():
                self._install_arm_layer()
                self.log.info("Installed the native AArch64 lsfg-vk layer for this host")
            else:
                self.log.info("Installed the x86-64 lsfg-vk layer for this host")
            content = ConfigurationManager.generate_toml_content_multi_profile(profile_data)
            self._validate_config_content(content)
            self._write_file(self.config_file_path, content, 0o644)
            return self._success_response(InstallationResponse, "lsfg-vk 2.0.0 installed successfully")
        except Exception as error:
            self.log.error(f"Error installing lsfg-vk: {error}")
            return self._error_response(InstallationResponse, str(error))

    def _install_arm_layer(self) -> None:
        """Replace the x86-64 layer with the native AArch64 build.

        The bundled generic archive only ships x86-64/x86 payloads, so an ARM
        host would otherwise end up with a layer it cannot load.  On ARM the
        x86 payload is dropped and the native layer plus its manifest are
        extracted from the ARM archive, keeping the destination filenames
        stable so the rest of the plugin needs no arch branching.
        """
        if not self.arm_archive_path.exists():
            raise FileNotFoundError(f"{ARM_ARCHIVE_FILENAME} not found at {self.arm_archive_path}")
        self._remove_if_exists(self.lib_x86_file)
        self._remove_if_exists(self.json_x86_file)
        with tarfile.open(self.arm_archive_path, "r:*") as archive:
            members = {
                member.name.removeprefix("./"): member
                for member in archive.getmembers()
                if member.isfile()
            }
            missing = [
                name
                for name in (ARM_LIB_SOURCE_FILENAME, ARM_MANIFEST_FILENAME)
                if name not in members
            ]
            if missing:
                raise OSError(
                    f"{ARM_ARCHIVE_FILENAME} is missing required files: " + ", ".join(missing)
                )
            self._extract_member(
                archive, members[ARM_LIB_SOURCE_FILENAME], self.lib_file, 0o644
            )
            self._extract_member(archive, members[ARM_MANIFEST_FILENAME], self.json_file, 0o644)
        self._align_manifest_library_path()
        self.log.info(f"Installed native AArch64 layer at {self.lib_file}")

    def _extract_member(self, archive: tarfile.TarFile, member, destination: Path, mode: int) -> None:
        source = archive.extractfile(member)
        if source is None:
            raise OSError(f"Could not read {member.name} from {ARM_ARCHIVE_FILENAME}")
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            with source:
                shutil.copyfileobj(source, temporary_file)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.chmod(mode)
        os.replace(temporary_path, destination)

    def _align_manifest_library_path(self) -> None:
        """Keep the installed manifest pointing at the layer actually installed.

        Upstream ships the ARM layer under its own filename while the plugin
        installs it under the shared name, so only the basename of the
        ``library_path`` needs to follow.  Anything unreadable is left alone:
        an unchanged manifest is no worse than a failed install.
        """
        try:
            manifest = json.loads(self.json_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        layer = manifest.get("layer") if isinstance(manifest, dict) else None
        if not isinstance(layer, dict) or "library_path" not in layer:
            return
        current_path = str(layer["library_path"])
        library_name = Path(current_path).name or current_path
        if library_name == self.lib_file.name:
            return
        layer["library_path"] = (
            current_path[: len(current_path) - len(library_name)] + self.lib_file.name
        )
        self.json_file.write_text(
            json.dumps(manifest, separators=(",", ":")) + "\n", encoding="utf-8"
        )

    def _validate_config_content(self, content: str) -> None:
        """Validate generated config, tolerating an unusable CLI on ARM hosts.

        lsfg-vk-cli is only shipped for x86-64, so an AArch64 host cannot run
        it.  The generated configuration is already schema-validated in
        process, so a CLI that cannot run must not fail the installation.
        """
        if self.runtime_service.cli_unusable():
            self.log.warning(
                f"Skipping lsfg-vk CLI validation because {CLI_FILENAME} cannot run on this host"
            )
            return
        self.runtime_service.validate_config_content(content)

    def _required_payload(self) -> tuple[Path, ...]:
        if self.is_arm_host():
            return (self.cli_file, self.lib_file, self.json_file, self.config_file_path)
        return (
            self.cli_file,
            self.lib_file,
            self.lib_x86_file,
            self.json_file,
            self.json_x86_file,
            self.config_file_path,
        )

    def _payload_destinations(self) -> Dict[str, tuple[Path, int]]:
        destinations = {
            f"bin/{CLI_FILENAME}": (self.cli_file, 0o755),
            f"bin/{UI_FILENAME}": (self.local_bin_dir / UI_FILENAME, 0o755),
            f"lib/{LIB_FILENAME}": (self.lib_file, 0o644),
            f"share/vulkan/implicit_layer.d/{JSON_FILENAME}": (self.json_file, 0o644),
            f"share/applications/{UI_DESKTOP_FILENAME}": (
                self.user_home / LOCAL_SHARE / "applications" / UI_DESKTOP_FILENAME,
                0o644,
            ),
            f"share/icons/hicolor/256x256/apps/{UI_ICON_FILENAME}": (
                self.user_home / LOCAL_SHARE / "icons/hicolor/256x256/apps" / UI_ICON_FILENAME,
                0o644,
            ),
        }
        if not self.is_arm_host():
            destinations[f"lib/{LIB_X86_FILENAME}"] = (self.lib_x86_file, 0o644)
            destinations[f"share/vulkan/implicit_layer.d/{JSON_X86_FILENAME}"] = (
                self.json_x86_file,
                0o644,
            )
        return destinations

    def _install_archive(self, archive_path: Path) -> None:
        destinations = self._payload_destinations()
        found = set()
        with tarfile.open(archive_path, "r:*") as archive:
            members = {
                member.name.removeprefix("./"): member
                for member in archive.getmembers()
                if member.isfile()
            }
            for source_path, (destination, mode) in destinations.items():
                member = members.get(source_path)
                if member is None:
                    continue
                source = archive.extractfile(member)
                if source is None:
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=destination.parent,
                        prefix=f".{destination.name}.",
                        delete=False,
                    ) as temporary_file:
                        temporary_path = Path(temporary_file.name)
                        with source:
                            shutil.copyfileobj(source, temporary_file)
                        temporary_file.flush()
                        os.fsync(temporary_file.fileno())
                    temporary_path.chmod(mode)
                    os.replace(temporary_path, destination)
                    found.add(source_path)
                except Exception:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
                    raise
        missing = sorted(set(destinations) - found)
        if missing:
            raise OSError("Archive is missing required files: " + ", ".join(missing))

    def _default_config(self) -> ProfileData:
        defaults = ConfigurationManager.get_defaults()
        return ProfileData(
            profiles={},
            global_config={"dll": defaults["dll"], "no_fp16": defaults["no_fp16"]},
        )

    def _prepare_config(self) -> ProfileData:
        try:
            profile_data = (
                ConfigurationManager.parse_toml_content_multi_profile(
                    self.config_file_path.read_text(encoding="utf-8")
                )
                if self.config_file_path.exists()
                else self._default_config()
            )
        except UnsupportedConfigurationVersion as error:
            if not ConfigurationManager.is_discardable_legacy_version(error.version):
                raise
            self._remove_if_exists(self.config_file_path)
            self.log.warning(
                f"Removed legacy lsfg-vk configuration version {error.version!r} during installation"
            )
            profile_data = self._default_config()
        except ValueError:
            profile_data = self._default_config()
        self._resolve_dll_path(profile_data)
        defaults = ConfigurationManager.get_defaults()
        for name, profile in profile_data["profiles"].items():
            profile_data["profiles"][name] = ConfigurationManager.validate_config(
                {**defaults, **profile, **profile_data["global_config"]}
            )
        return profile_data

    def _resolve_dll_path(self, profile_data: ProfileData) -> bool:
        current_path = str(profile_data["global_config"].get("dll") or "")
        if current_path and Path(current_path).is_file():
            return False
        dll_path = self.steam_service.find_lsfg_vk_dll()
        if dll_path and current_path != dll_path:
            profile_data["global_config"]["dll"] = dll_path
            return True
        return False

    def _repair_dll_path(self) -> None:
        if not self.config_file_path.is_file():
            return
        try:
            dll_path = self.steam_service.find_lsfg_vk_dll()
            if not dll_path:
                return
            profile_data = ConfigurationManager.parse_toml_content_multi_profile(
                self.config_file_path.read_text(encoding="utf-8")
            )
            if profile_data["global_config"].get("dll") == dll_path:
                return
            profile_data["global_config"]["dll"] = dll_path
            content = ConfigurationManager.generate_toml_content_multi_profile(profile_data)
            self.runtime_service.validate_config_content(content)
            self._write_file(self.config_file_path, content, 0o644)
            self.log.info(f"Repaired lsfg-vk DLL path: {dll_path}")
        except Exception as error:
            self.log.warning(f"Could not repair lsfg-vk DLL path: {error}")

    def _remove_legacy_layer_files(self) -> None:
        for path in (self.legacy_lib_file, self.legacy_json_file):
            self._remove_if_exists(path)

    def _prune_empty_directories(self) -> None:
        # Only prune directories created by this plugin.  Never remove a
        # non-empty directory because the user's other tools may use it.
        candidates = (
            self.config_dir,
            self.local_bin_dir,
            self.local_lib_dir,
            self.local_share_dir,
            self.user_home / LOCAL_SHARE / "applications",
            self.user_home / LOCAL_SHARE / "icons/hicolor/256x256/apps",
        )
        for directory in candidates:
            try:
                if directory.is_dir() and not directory.is_symlink():
                    directory.rmdir()
                    self.log.info(f"Removed empty directory {directory}")
            except OSError:
                continue

    def check_installation(self) -> InstallationCheckResponse:
        try:
            installed = all(path.is_file() for path in self._required_payload())
            if installed:
                self._repair_dll_path()
            if self.runtime_service.cli_unusable():
                lossless_scaling = self._check_lossless_scaling_without_cli()
            else:
                lossless_scaling = self.runtime_service.check_lossless_scaling()
            return {
                "installed": installed,
                "lossless_scaling_installed": bool(lossless_scaling["installed"]),
                "lossless_scaling_status": str(lossless_scaling["status"]),
                "error": None,
            }
        except Exception as error:
            return {
                "installed": False,
                "lossless_scaling_installed": False,
                "lossless_scaling_status": str(error),
                "error": str(error),
            }

    def _lossless_scaling_unavailable_reason(self) -> Dict[str, str]:
        """Explain why Lossless Scaling is not usable, without leaking paths.

        The Steam branch is what lsfg-vk itself gates the DLL on, so it is
        reported ahead of a plain "missing DLL" so the user knows what to fix.
        """
        try:
            branch = self.steam_service.get_branch_status()
            if not isinstance(branch, dict):
                branch = {}
        except Exception:
            branch = {}
        if branch.get("needs_switch") is True:
            message = str(branch.get("message") or "").strip()
            target = str(branch.get("target_branch") or "lsfg-vk")
            return {
                "installed": False,
                "status": message or f"Select the {target} branch of Lossless Scaling in Steam",
            }
        return {
            "installed": False,
            "status": self.runtime_service.DLL_NOT_FOUND_STATUS,
        }

    def _check_lossless_scaling_without_cli(self) -> Dict[str, str]:
        """Report Lossless Scaling state using only filesystem probes.

        lsfg-vk-cli is an x86-64 binary, so an AArch64 host cannot run it.  The
        DLL is the sole real prerequisite for the layer, and the Steam branch
        gating around it is already answered by SteamService, so the CLI is not
        needed to tell the user whether the runtime is ready.

        ``steam_service.find_lsfg_vk_dll()`` is the single discovery helper and
        the only one that applies the Steam branch gate, so its answer is
        final: a DLL left in conf.toml by another branch must not read as
        ready, which is exactly what the CLI would catch on x86-64.  That is
        why the configured path is never trusted on its own here.
        """
        discovered = self.steam_service.find_lsfg_vk_dll() or ""
        if discovered and Path(discovered).is_file():
            return {
                "installed": True,
                "status": RuntimeService.CLI_UNSUPPORTED_STATUS_DETECTED,
            }
        return self._lossless_scaling_unavailable_reason()

    def uninstall(self) -> UninstallationResponse:
        try:
            removed = [
                str(path)
                for path in (
                    self.lib_file,
                    self.lib_x86_file,
                    self.json_file,
                    self.json_x86_file,
                    self.cli_file,
                    self.local_bin_dir / UI_FILENAME,
                    self.user_home / LOCAL_SHARE / "applications" / UI_DESKTOP_FILENAME,
                    self.user_home / LOCAL_SHARE / "icons/hicolor/256x256/apps" / UI_ICON_FILENAME,
                    self.legacy_lib_file,
                    self.legacy_json_file,
                    self.legacy_script_path,
                    self.config_file_path,
                )
                if self._remove_if_exists(path)
            ]
            self._prune_empty_directories()
            if not removed:
                return self._success_response(
                    UninstallationResponse,
                    "No lsfg-vk files found to remove",
                    removed_files=None,
                )
            return self._success_response(
                UninstallationResponse,
                f"lsfg-vk uninstalled successfully. Removed {len(removed)} files.",
                removed_files=removed,
            )
        except Exception as error:
            return self._error_response(
                UninstallationResponse,
                str(error),
                removed_files=None,
            )

    def cleanup_on_uninstall(self) -> bool:
        try:
            result = self.uninstall()
            if not result.get("success"):
                self.log.error(f"Error cleaning up lsfg-vk files during uninstall: {result.get('error')}")
                return False
            return True
        except Exception as error:
            self.log.error(f"Error cleaning up lsfg-vk files during uninstall: {error}")
            self.log.error(traceback.format_exc())
            return False
