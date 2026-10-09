import json
import os
from pathlib import Path
from typing import Any, Dict

import decky

from .configuration import ConfigurationService
from .flatpak_profile_service import FlatpakProfileService
from .flatpak_service import FlatpakService
from .installation import InstallationService
from .runtime_service import RuntimeService
from .steam_service import SteamService
from .wrapper_service import WrapperService


class Plugin:
    def __init__(self):
        self.runtime_service = RuntimeService()
        self.steam_service = SteamService()
        self.installation_service = InstallationService(
            runtime_service=self.runtime_service,
            steam_service=self.steam_service,
        )
        self.configuration_service = ConfigurationService(runtime_service=self.runtime_service)
        self.flatpak_service = FlatpakService()
        self.flatpak_profile_service = FlatpakProfileService(
            self.flatpak_service,
            self.configuration_service,
        )
        self.wrapper_service = WrapperService()

    async def install_lsfg_vk(self):
        return self.installation_service.install()

    async def check_lsfg_vk_installed(self):
        return self.installation_service.check_installation()

    def _cleanup_runtime_state(self, preserve_wrapper: bool = False):
        flatpak = self.flatpak_service.remove_plugin_owned_environment()
        if not flatpak.get("success"):
            return flatpak.get("error") or "Could not clean up Flatpak support"
        profiles = self.configuration_service.reset_all_flatpak_configs()
        if not profiles.get("success"):
            return profiles.get("error") or "Could not remove Flatpak profiles"
        wrapper = self.wrapper_service.neutralize() if preserve_wrapper else self.wrapper_service.purge()
        if not wrapper.get("success"):
            return wrapper.get("error") or "Could not remove workaround state"
        return None

    async def uninstall_lsfg_vk(self):
        error = self._cleanup_runtime_state()
        if error:
            return {
                "success": False,
                "message": "",
                "error": error,
                "removed_files": None,
            }
        return self.installation_service.uninstall()

    async def get_game_configs(self):
        return self.configuration_service.get_game_configs()

    async def update_global_config(self, config: Dict[str, Any]):
        return self.configuration_service.update_global_config(config)

    async def get_installed_games(self):
        return self.steam_service.get_installed_games()

    async def update_game_config(self, appid: str, game_name: str, config: Dict[str, Any]):
        return self.configuration_service.update_game_config(appid, game_name, config)

    async def reset_game_config(self, appid: str):
        return self.configuration_service.reset_game_config(appid)

    async def reset_game_configs(self, appids):
        return self.configuration_service.reset_game_configs(appids)

    async def reset_all_game_configs(self):
        return self.configuration_service.reset_all_game_configs()

    async def get_workaround_state(self, appid: str):
        return self.wrapper_service.get(appid)

    async def get_workaround_apps(self):
        return self.wrapper_service.list_apps()

    async def set_workaround_state(
        self,
        appid: str,
        state: Dict[str, Any],
        command_token_added: bool = False,
        non_steam: bool = False,
    ):
        return self.wrapper_service.set(appid, state, command_token_added, non_steam)

    async def remove_workaround_state(self, appid: str):
        return self.wrapper_service.remove(appid)

    async def get_config_file_content(self):
        path = self.configuration_service.config_file_path
        try:
            if not path.exists():
                return {
                    "success": False,
                    "content": None,
                    "path": str(path),
                    "error": "Config file does not exist",
                }
            return {
                "success": True,
                "content": path.read_text(encoding="utf-8"),
                "path": str(path),
                "error": None,
            }
        except Exception as error:
            return {
                "success": False,
                "content": None,
                "path": str(path),
                "error": f"Error reading config file: {error}",
            }

    async def get_debug_file_contents(self):
        files = (
            ("config", "LSFG-VK configuration", self.configuration_service.config_file_path),
            ("workarounds", "Per-app workarounds", self.wrapper_service.sidecar_path),
            ("wrapper", "Generated launch wrapper", self.wrapper_service.wrapper_path),
            ("flatpak", "Flatpak ownership state", self.flatpak_service.ownership_path),
        )
        contents = []
        for file_id, label, path in files:
            item = {
                "id": file_id,
                "label": label,
                "path": str(path),
                "exists": False,
                "content": None,
                "error": None,
            }
            try:
                if path.is_symlink():
                    item["error"] = "Path is a symlink; refusing to read it"
                elif not path.exists():
                    item["error"] = "File does not exist"
                elif not path.is_file():
                    item["error"] = "Path is not a regular file"
                else:
                    item["exists"] = True
                    item["content"] = path.read_text(encoding="utf-8")
            except Exception as error:
                item["error"] = f"Error reading file: {error}"
            contents.append(item)
        return {
            "success": True,
            "message": "Debug file contents retrieved",
            "error": None,
            "files": contents,
            "diagnostics": self._collect_diagnostics(),
        }

    def _collect_diagnostics(self) -> Dict[str, Any]:
        """Gather the runtime state that explains why frame generation is inert.

        Most reported failures come from the layer never being handed a
        usable configuration at launch time, which no single file reveals, so
        the export carries the whole picture in one place.
        """
        diagnostics: Dict[str, Any] = {}
        try:
            diagnostics["host_is_arm"] = self.installation_service.is_arm_host()
        except Exception as error:
            diagnostics["host_is_arm"] = f"unknown ({error})"
        try:
            diagnostics["cli_usable"] = not self.runtime_service.cli_unusable()
        except Exception as error:
            diagnostics["cli_usable"] = f"unknown ({error})"
        try:
            diagnostics["lossless_scaling"] = self.runtime_service.check_lossless_scaling()
        except Exception as error:
            diagnostics["lossless_scaling"] = {"installed": False, "status": str(error)}
        try:
            diagnostics["branch_status"] = self.steam_service.get_branch_status()
        except Exception as error:
            diagnostics["branch_status"] = {"status": str(error)}

        payload = {}
        for name, path in (
            ("config", self.configuration_service.config_file_path),
            ("wrapper", self.wrapper_service.wrapper_path),
            ("layer", self.installation_service.lib_file),
            ("x86_layer", self.installation_service.lib_x86_file),
            ("manifest", self.installation_service.json_file),
            ("x86_manifest", self.installation_service.json_x86_file),
            ("cli", self.installation_service.cli_file),
        ):
            try:
                payload[name] = str(path) if path else None
            except Exception:
                payload[name] = None
        diagnostics["payload_paths"] = payload

        # Whether the launch wrapper actually exports the configuration is the
        # single most common cause of a silently inert multiplier, so the
        # rendered script is reported alongside its mode.
        try:
            wrapper_path = self.wrapper_service.wrapper_path
            if wrapper_path.is_file() and not wrapper_path.is_symlink():
                diagnostics["wrapper_exports_config"] = (
                    "LSFGVK_CONFIG" in wrapper_path.read_text(encoding="utf-8")
                )
            else:
                diagnostics["wrapper_exports_config"] = "wrapper is missing"
        except Exception as error:
            diagnostics["wrapper_exports_config"] = f"unknown ({error})"

        try:
            diagnostics["plugin_version"] = self._plugin_version()
        except Exception:
            diagnostics["plugin_version"] = "unknown"
        return diagnostics

    @staticmethod
    def _plugin_version() -> str:
        import json

        manifest = Path(__file__).resolve().parent.parent.parent / "package.json"
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "unknown"))

    def _report_target(self) -> Path:
        """Pick a location the user can actually reach.

        Decky runs as a system service, so /tmp is frequently a private
        namespace and the report would vanish from the user's point of view.
        The user's Downloads directory is shared, so it is used when present
        and the home directory is the fallback.
        """
        home = self.installation_service.user_home
        candidate = home / "Downloads"
        if candidate.is_dir():
            return candidate / "lsfg-vk-debug-report.txt"
        return home / "lsfg-vk-debug-report.txt"

    async def write_debug_report(self) -> Dict[str, Any]:
        """Write a debug report the user can hand to a maintainer.

        Returns the path, the report text and where it actually landed, so the
        frontend can tell the user rather than staying silent on failure.
        """
        payload = await self.get_debug_file_contents()
        lines = [
            "lsfg-vk Decky plugin debug report",
            "=" * 40,
            "",
        ]

        for key, value in (payload.get("diagnostics") or {}).items():
            lines.append(f"[{key}]")
            if isinstance(value, (dict, list)):
                lines.append(json.dumps(value, indent=2, sort_keys=True, default=str))
            else:
                lines.append(str(value))
            lines.append("")

        for item in payload.get("files") or []:
            if not isinstance(item, dict):
                continue
            lines.append(f"--- {item.get('label')} ({item.get('id')}) ---")
            lines.append(f"path: {item.get('path')}")
            if item.get("exists") and item.get("content"):
                lines.append(item["content"])
            else:
                lines.append(f"unavailable: {item.get('error') or 'not present'}")
            lines.append("")

        report = "\n".join(line.rstrip() for line in lines) + "\n"
        target = self._report_target()
        location = "Downloads" if target.parent.name == "Downloads" else "home"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report, encoding="utf-8")
        except Exception as error:
            return {
                "success": False,
                "error": f"Could not write the report: {error}",
                "path": str(target),
                "location": location,
                "report": report,
            }
        return {
            "success": True,
            "error": None,
            "path": str(target),
            "location": location,
            "report": report,
        }

    async def get_lossless_scaling_branch_status(self):
        return self.steam_service.get_branch_status()

    async def get_flatpak_apps(self):
        return self.flatpak_profile_service.get_apps()

    async def enable_flatpak_app(self, flatpak_app_id: str):
        return self.flatpak_profile_service.enable_app(flatpak_app_id)

    async def update_flatpak_config(self, flatpak_app_id: str, config: Dict[str, Any]):
        return self.flatpak_profile_service.update_config(flatpak_app_id, config)

    async def get_flatpak_workaround_state(self, flatpak_app_id: str):
        return self.flatpak_profile_service.get_workaround_state(flatpak_app_id)

    async def set_flatpak_workaround_state(self, flatpak_app_id: str, state: Dict[str, Any]):
        return self.flatpak_profile_service.set_workaround_state(flatpak_app_id, state)

    async def remove_flatpak_app(self, flatpak_app_id: str):
        return self.flatpak_profile_service.remove_app(flatpak_app_id)

    async def get_running_flatpak_apps(self):
        return self.flatpak_profile_service.get_running_apps()

    async def _main(self):
        repair = self.wrapper_service.repair()
        if not repair.get("success"):
            decky.logger.error(f"Could not repair lsfg workaround wrapper: {repair.get('error')}")
        decky.logger.info("decky-lsfg-vk plugin loaded")

    async def _unload(self):
        decky.logger.info("decky-lsfg-vk plugin unloaded")

    async def _uninstall(self):
        decky.logger.info("decky-lsfg-vk plugin being uninstalled")
        try:
            error = self._cleanup_runtime_state(preserve_wrapper=True)
            if error:
                decky.logger.warning(f"Preserving lsfg-vk files because uninstall cleanup failed: {error}")
                return
        except Exception as error:
            decky.logger.error(f"Error during lsfg-vk cleanup: {error}")
            return
        self.installation_service.cleanup_on_uninstall()
        decky.logger.info("decky-lsfg-vk plugin uninstall cleanup completed")

    async def _migration(self):
        decky.logger.info("Running decky-lsfg-vk plugin migrations")
        decky.migrate_logs(os.path.join(
            decky.DECKY_USER_HOME,
            ".config",
            "decky-lossless-scaling-vk",
            "lossless-scaling-vk.log",
        ))
        decky.migrate_settings(
            os.path.join(decky.DECKY_HOME, "settings", "lossless-scaling-vk.json"),
            os.path.join(decky.DECKY_USER_HOME, ".config", "decky-lossless-scaling-vk"),
        )
        decky.migrate_runtime(
            os.path.join(decky.DECKY_HOME, "lossless-scaling-vk"),
            os.path.join(decky.DECKY_USER_HOME, ".local", "share", "decky-lossless-scaling-vk"),
        )
        decky.logger.info("decky-lsfg-vk plugin migrations completed")