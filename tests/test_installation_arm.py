import io
import json
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


sys.modules.setdefault(
    "decky",
    types.SimpleNamespace(DECKY_USER_HOME="/home/deck", logger=Mock()),
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from lsfg_vk.base_service import BaseService
from lsfg_vk.constants import (
    ARM_ARCHIVE_FILENAME,
    ARM_LIB_SOURCE_FILENAME,
    ARMADA_DEVICE_ENV,
    ARMADA_GAME_LAUNCH,
)
from lsfg_vk.configuration import ConfigurationService
from lsfg_vk.installation import InstallationService
from lsfg_vk.runtime_service import RuntimeService
from lsfg_vk.wrapper_service import WrapperService


AArch64_ELF_CLASS = 2  # ELFCLASS64
LITTLE_ENDIAN = 1  # ELFDATA2LSB
EM_X86_64 = 62
EM_AARCH64 = 183


def _elf_header(machine: int, elf_class: int = AArch64_ELF_CLASS) -> bytes:
    """Build a minimal ELF64 header, since only e_machine is inspected."""
    return (
        b"\x7fELF"
        + bytes([elf_class, LITTLE_ENDIAN, 1, 0])
        + b"\x00" * 8
        + (3).to_bytes(2, "little")  # e_type: ET_DYN
        + machine.to_bytes(2, "little")  # e_machine
        + b"\x00" * 8
    )


ARM_LAYER_BYTES = _elf_header(EM_AARCH64)


def _arm_archive(path: Path) -> Path:
    """Build a stand-in for the upstream aarch64 layer archive."""
    archive = path / ARM_ARCHIVE_FILENAME
    with tarfile.open(archive, "w:xz") as bundle:
        import io

        def add(name: str, content: bytes) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            bundle.addfile(info, io.BytesIO(content))

        add(ARM_LIB_SOURCE_FILENAME, ARM_LAYER_BYTES)
        add(
            "VkLayer_LSFGVK_frame_generation.json",
            (
                '{"file_format_version":"1.1.0","layer":{"name":"VK_LAYER_LSFGVK_frame_generation",'
                '"description":"Lossless Scaling frame generation layer",'
                f'"implementation_version":"2","library_path":"{ARM_LIB_SOURCE_FILENAME}","type":"GLOBAL",'
                '"api_version":"1.4.328","disable_environment":{"DISABLE_LSFGVK":"1"}}}'
            ).encode(),
        )
    return archive


class ArmArchitectureDetectionTests(unittest.TestCase):
    """The native host must be identified even when Decky runs under FEX."""

    def _detect(self, machine: str, armada_marker: bool = False) -> bool:
        with unittest.mock.patch("lsfg_vk.base_service.platform.machine", return_value=machine):
            with unittest.mock.patch(
                "lsfg_vk.base_service.ARMADA_DEVICE_ENV",
                Mock(is_file=Mock(return_value=armada_marker)),
            ):
                return BaseService._detect_arm_architecture()

    def test_detects_aarch64_from_platform_machine(self):
        self.assertTrue(self._detect("aarch64"))
        self.assertTrue(self._detect("arm64"))
        self.assertFalse(self._detect("x86_64"))

    def test_detects_armada_host_when_python_reports_x86_under_fex(self):
        # Decky's python reports x86_64 on Armada because it runs through FEX.
        self.assertTrue(self._detect("x86_64", armada_marker=True))

    def test_unarmada_host_reported_as_x86(self):
        self.assertFalse(self._detect("x86_64", armada_marker=False))

    def _detect_with_pid1(self, machine: str, pid1_header) -> bool:
        armada = unittest.mock.patch(
            "lsfg_vk.base_service.ARMADA_DEVICE_ENV",
            Mock(is_file=Mock(return_value=False)),
        )
        machine_patch = unittest.mock.patch(
            "lsfg_vk.base_service.platform.machine", return_value=machine
        )
        armada.start()
        machine_patch.start()
        self.addCleanup(armada.stop)
        self.addCleanup(machine_patch.stop)

        if pid1_header is None:
            open_patch = unittest.mock.patch(
                "lsfg_vk.base_service.Path.open", side_effect=OSError("unreadable")
            )
        else:

            def _open(*args, **kwargs):
                return io.BytesIO(pid1_header)

            open_patch = unittest.mock.patch(
                "lsfg_vk.base_service.Path.open", _open
            )
        open_patch.start()
        self.addCleanup(open_patch.stop)
        return BaseService._detect_arm_architecture()

    def test_detects_native_aarch64_from_pid_one_elf_header(self):
        # Decky under FEX reports x86_64, and the Armada marker is absent on
        # other ARM handhelds, so PID 1's ELF header is the last resort.
        self.assertTrue(self._detect_with_pid1("x86_64", _elf_header(EM_AARCH64)))

    def test_pid_one_elf_header_for_x86_64_is_not_arm(self):
        self.assertFalse(self._detect_with_pid1("x86_64", _elf_header(EM_X86_64)))

    def test_pid_one_elf_class32_is_not_arm(self):
        elf32_class = 1  # ELFCLASS32
        self.assertFalse(self._detect_with_pid1("x86_64", _elf_header(EM_AARCH64, elf32_class)))

    def test_short_pid_one_header_is_not_arm(self):
        self.assertFalse(self._detect_with_pid1("x86_64", _elf_header(EM_AARCH64)[:10]))

    def test_unreadable_pid_one_is_not_arm(self):
        self.assertFalse(self._detect_with_pid1("x86_64", None))

    def test_unreadable_armada_marker_is_not_arm(self):
        with unittest.mock.patch("lsfg_vk.base_service.platform.machine", return_value="x86_64"):
            with unittest.mock.patch(
                "lsfg_vk.base_service.ARMADA_DEVICE_ENV",
                Mock(is_file=Mock(side_effect=OSError("permission denied"))),
            ):
                self.assertFalse(BaseService._detect_arm_architecture())

    def test_is_arm_host_resolves_through_the_process_wide_cache(self):
        from lsfg_vk import base_service

        base_service._host_is_aarch64.cache_clear()
        try:
            service = InstallationService.__new__(InstallationService)
            BaseService.__init__(service)
            service.log = Mock()
            with unittest.mock.patch.object(
                BaseService, "_detect_arm_architecture", return_value=True
            ) as detect:
                self.assertTrue(service.is_arm_host())
                self.assertTrue(service.is_arm_host())
            detect.assert_called_once()
        finally:
            base_service._host_is_aarch64.cache_clear()

    def test_host_is_aarch64_helper_is_cached(self):
        from lsfg_vk import base_service

        base_service._host_is_aarch64.cache_clear()
        try:
            with unittest.mock.patch(
                "lsfg_vk.base_service.platform.machine", return_value="aarch64"
            ):
                self.assertTrue(base_service._host_is_aarch64())
            # A second call must not re-probe the host.
            with unittest.mock.patch(
                "lsfg_vk.base_service.platform.machine", return_value="x86_64"
            ):
                self.assertTrue(base_service._host_is_aarch64())
        finally:
            base_service._host_is_aarch64.cache_clear()


def _build_service(home: Path, bin_dir: Path) -> tuple[InstallationService, Mock, Mock]:
    """Build an InstallationService rooted at ``home`` with the payload paths."""
    steam = Mock()
    steam.find_lsfg_vk_dll.return_value = None
    runtime = Mock()
    runtime.check_lossless_scaling.return_value = {
        "installed": True,
        "status": "Lossless Scaling detected by lsfg-vk",
    }
    service = InstallationService(
        logger=Mock(),
        runtime_service=runtime,
        steam_service=steam,
    )
    service.user_home = home
    service.local_bin_dir = home / ".local/bin"
    service.local_lib_dir = home / ".local/lib"
    service.local_share_dir = home / ".local/share/vulkan/implicit_layer.d"
    service.config_dir = home / ".config/lsfg-vk"
    service.config_file_path = service.config_dir / "conf.toml"
    service.cli_file = service.local_bin_dir / "lsfg-vk-cli"
    service.lib_file = service.local_lib_dir / "liblsfg-vk-layer.so"
    service.lib_x86_file = service.local_lib_dir / "liblsfg-vk-layer.x86.so"
    service.json_file = service.local_share_dir / "VkLayer_LSFGVK_frame_generation.json"
    service.json_x86_file = service.local_share_dir / "VkLayer_LSFGVK_frame_generation.x86.json"
    service.legacy_lib_file = service.local_lib_dir / "liblsfg-vk.so"
    service.legacy_json_file = service.local_share_dir / "VkLayer_LS_frame_generation.json"
    service.arm_archive_path = _arm_archive(bin_dir)
    return service, runtime, steam


class ArmInstallationTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.home = root / "home" / "deck"
        self.bin = root / "bin"
        self.home.mkdir(parents=True)
        self.bin.mkdir(parents=True)
        self.service, self.runtime, self.steam = _build_service(self.home, self.bin)

    def tearDown(self):
        self.tempdir.cleanup()

    def _generic_archive(self, with_x86: bool = True) -> Path:
        import io

        archive = self.bin / "lsfg-vk-2.0.0.tar.xz"
        with tarfile.open(archive, "w:xz") as bundle:

            def add(name, content):
                info = tarfile.TarInfo(name)
                info.size = len(content)
                bundle.addfile(info, io.BytesIO(content))

            add("lib/liblsfg-vk-layer.so", b"x86 layer")
            if with_x86:
                add("lib/liblsfg-vk-layer.x86.so", b"x86 32 layer")
            add("bin/lsfg-vk-cli", b"x86 cli")
            add("share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.json",
                b'{"layer":{"library_path":"../../../lib/liblsfg-vk-layer.so"}}')
            if with_x86:
                add("share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.x86.json",
                    b'{"layer":{"library_path":"../../../lib/liblsfg-vk-layer.x86.so"}}')
            add("bin/lsfg-vk-ui", b"x86 ui")
            add("share/applications/gay.pancake.lsfg-vk-ui.desktop", b"[Desktop Entry]")
            add("share/icons/hicolor/256x256/apps/gay.pancake.lsfg-vk-ui.png", b"png")
        return archive

    def _patch_arch(self, is_arm: bool):
        patcher = unittest.mock.patch.object(InstallationService, "is_arm_host", return_value=is_arm)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_arm_install_replaces_x86_layer_and_drops_x86_payload(self):
        self._patch_arch(True)
        self._generic_archive()
        # Simulate a leftover x86 install on this ARM host before the fix.
        self.service.lib_x86_file.parent.mkdir(parents=True, exist_ok=True)
        self.service.lib_x86_file.write_bytes(b"x86 32 layer")
        self.service.json_x86_file.parent.mkdir(parents=True, exist_ok=True)
        self.service.json_x86_file.write_text("{}", encoding="utf-8")

        self.service._install_arm_layer()

        self.assertFalse(self.service.lib_x86_file.exists())
        self.assertFalse(self.service.json_x86_file.exists())
        self.assertTrue(self.service.lib_file.is_file())
        self.assertEqual(self.service.lib_file.read_bytes(), ARM_LAYER_BYTES)
        self.assertEqual(self.service.lib_file.stat().st_mode & 0o777, 0o644)

    def test_arm_payload_excludes_x86_layer(self):
        self._patch_arch(True)
        destinations = self.service._payload_destinations()
        self.assertIn("lib/liblsfg-vk-layer.so", destinations)
        self.assertNotIn("lib/liblsfg-vk-layer.x86.so", destinations)
        self.assertNotIn("share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.x86.json", destinations)

    def test_x86_payload_unchanged(self):
        self._patch_arch(False)
        destinations = self.service._payload_destinations()
        self.assertIn("lib/liblsfg-vk-layer.so", destinations)
        self.assertIn("lib/liblsfg-vk-layer.x86.so", destinations)
        self.assertIn("share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.json", destinations)
        self.assertIn("share/vulkan/implicit_layer.d/VkLayer_LSFGVK_frame_generation.x86.json", destinations)

    def test_required_payload_is_arch_aware(self):
        self._patch_arch(True)
        required = self.service._required_payload()
        self.assertNotIn(self.service.lib_x86_file, required)
        self.assertNotIn(self.service.json_x86_file, required)
        self.assertIn(self.service.lib_file, required)
        self.assertIn(self.service.cli_file, required)
        self.assertIn(self.service.config_file_path, required)

        self._patch_arch(False)
        required = self.service._required_payload()
        self.assertIn(self.service.lib_x86_file, required)
        self.assertIn(self.service.json_x86_file, required)

    def _install_payload(self, arm: bool):
        self._patch_arch(arm)
        for path in self.service._required_payload():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("installed", encoding="utf-8")

    def test_check_installation_true_on_arm_without_x86_payload(self):
        """The x86 payload is not expected on ARM, so `installed` must hold."""
        self._install_payload(arm=True)

        result = self.service.check_installation()

        self.assertTrue(result["installed"])
        # No DLL exists in this fixture, so the runtime is not ready yet, but
        # the payload check itself must not be the thing that says "not ready".
        self.assertFalse(result["lossless_scaling_installed"])
        self.assertNotIn("x86_64", result["lossless_scaling_status"])
        self.assertTrue(result["lossless_scaling_status"])

    def test_check_installation_false_on_arm_when_layer_is_missing(self):
        self._install_payload(arm=True)
        self.service.lib_file.unlink()

        result = self.service.check_installation()

        self.assertFalse(result["installed"])

    def test_check_installation_false_on_x86_when_x86_layer_missing(self):
        self._install_payload(arm=False)
        self.service.lib_x86_file.unlink()

        result = self.service.check_installation()

        self.assertFalse(result["installed"])

    def test_check_installation_true_on_x86_with_full_payload(self):
        self._install_payload(arm=False)

        result = self.service.check_installation()

        self.assertTrue(result["installed"])

    def test_arm_install_skips_cli_validation_when_cli_cannot_run(self):
        self._patch_arch(True)
        self.runtime.cli_unusable.return_value = True

        self.service._validate_config_content("version = 2\n")

        self.runtime.validate_config_content.assert_not_called()

    def test_install_still_validates_through_cli_when_usable(self):
        self._patch_arch(False)
        self.runtime.cli_unusable.return_value = False

        self.service._validate_config_content("version = 2\n")

        self.runtime.validate_config_content.assert_called_once_with("version = 2\n")

    def test_arm_install_rejects_missing_arm_archive(self):
        self._patch_arch(True)
        self.service.arm_archive_path = self.bin / "missing.tar.xz"
        with self.assertRaises(FileNotFoundError):
            self.service._install_arm_layer()

    def test_manifest_resolves_to_the_installed_layer_directory(self):
        """The manifest must point at the directory the layer was installed in.

        Upstream's ARM manifest uses a bare filename, which the Vulkan loader
        resolves against the manifest's own directory -- not the library one.
        The layer was therefore never found, and no profile ever loaded.
        """
        self._patch_arch(True)
        self._generic_archive()
        self.service._install_archive(self.bin / "lsfg-vk-2.0.0.tar.xz")
        self.service._install_arm_layer()

        manifest = json.loads(self.service.json_file.read_text(encoding="utf-8"))
        library_path = Path(manifest["layer"]["library_path"])
        # A relative path must survive being resolved from the manifest's dir.
        resolved = (self.service.json_file.parent / library_path).resolve()
        self.assertEqual(resolved, self.service.lib_file.resolve())

    def test_manifest_never_points_at_uninstalled_library(self):
        self._patch_arch(True)
        self._generic_archive()
        self.service._install_archive(self.bin / "lsfg-vk-2.0.0.tar.xz")
        self.service._install_arm_layer()
        manifest = self.service.json_file.read_text(encoding="utf-8")

        self.assertIn(self.service.lib_file.name, manifest)
        self.assertNotIn("liblsfg-vk-layer.x86.so", manifest)
        self.assertFalse(self.service.json_x86_file.exists())


class ArmadaLaunchWrapperTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home" / "deck"
        self.home.mkdir(parents=True)
        self.service = WrapperService(logger=Mock())
        self.service.user_home = self.home
        self.service.config_dir = self.home / ".config/lsfg-vk"
        self.service.config_file_path = self.service.config_dir / "conf.toml"
        self.service.sidecar_path = self.service.config_dir / "workarounds.json"
        self.service.wrapper_path = self.home / ".lsfg"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_exec_block_wraps_armada_game_launch_when_present(self):
        lines = WrapperService._generate_game_launch_lines()

        self.assertIn(f'armada_game_launch="{ARMADA_GAME_LAUNCH.as_posix()}"', lines)
        self.assertIn(
            f'if [ -f "{ARMADA_DEVICE_ENV.as_posix()}" ] && [ -x "$armada_game_launch" ]; then',
            lines,
        )
        self.assertIn('    exec "$armada_game_launch" "$@"', lines)
        self.assertEqual(lines[-1], 'exec "$@"')

    def test_exec_block_is_idempotent_when_armada_already_in_argv(self):
        lines = WrapperService._generate_game_launch_lines()

        self.assertIn('for argument in "$@"; do', lines)
        self.assertIn('    if [ "$argument" = "$armada_game_launch" ]; then', lines)

    def test_rendered_wrapper_is_valid_shell_and_keeps_dispatch(self):
        self.service.set("123", self.service.default_state())
        content = self.service.wrapper_path.read_text(encoding="utf-8")

        self.assertEqual(subprocess.run(["/bin/sh", "-n", str(self.service.wrapper_path)]).returncode, 0)
        self.assertIn('case "$appid" in', content)
        self.assertIn("armada_game_launch", content)
        self.assertIn('exec "$@"', content)

    def _exec_block_script(self, armada_path: Path) -> Path:
        """Render the exec block against a stubbed Armada path."""
        script = self.home / ".lsfg-exec"
        body = "\n".join(
            line.replace(ARMADA_GAME_LAUNCH.as_posix(), str(armada_path))
            for line in WrapperService._generate_game_launch_lines()
        )
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)
        return script

    def test_exec_directly_when_armada_already_rewrote_the_command(self):
        """Armada re-entering the wrapper must not wrap the command twice."""
        armada = self.home / "armada-game-launch"
        armada.write_text('#!/bin/sh\nexec "$@"\n', encoding="utf-8")
        armada.chmod(0o755)
        script = self._exec_block_script(armada)

        result = subprocess.run(
            [str(script), str(armada), "/usr/bin/env"],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.returncode, 0)
        # env ran directly, so armada's marker never reached the output.
        self.assertIn("PATH=", result.stdout)

    def test_wrapper_wraps_armada_when_both_marker_and_wrapper_exist(self):
        armada = self.home / "armada-game-launch"
        armada.write_text(
            '#!/bin/sh\necho "wrapped: $*"\nexec "$@"\n', encoding="utf-8",
        )
        armada.chmod(0o755)
        script = self._exec_block_script(armada)
        device_env = self.home / "device-env"
        device_env.write_text("", encoding="utf-8")
        body = "\n".join(
            line
            .replace(ARMADA_GAME_LAUNCH.as_posix(), str(armada))
            .replace(ARMADA_DEVICE_ENV.as_posix(), str(device_env))
            for line in WrapperService._generate_game_launch_lines()
        )
        script.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
        script.chmod(0o755)

        result = subprocess.run(
            [str(script), "/usr/bin/env"],
            env={"PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertIn("wrapped: /usr/bin/env", result.stdout)

    def test_wrapper_falls_back_to_plain_exec_without_armada(self):
        armada = self.home / "armada-game-launch"
        script = self._exec_block_script(armada)

        result = subprocess.run(
            [str(script), "/usr/bin/env"],
            env={"PATH": "/usr/bin:/bin", "ArmadaProbe": "unwrapped"},
            capture_output=True,
            text=True,
            check=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("ArmadaProbe=unwrapped", result.stdout)


class ArmRuntimeTests(unittest.TestCase):
    """CLI-backed features must degrade instead of hard-failing on ARM."""

    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home" / "deck"
        self.home.mkdir(parents=True)
        self.runtime = Mock()
        self.configuration = ConfigurationService(runtime_service=self.runtime)
        self.configuration.user_home = self.home
        self.configuration.config_dir = self.home / ".config/lsfg-vk"
        self.configuration.config_file_path = self.configuration.config_dir / "conf.toml"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_saving_profiles_skips_validation_when_cli_cannot_run(self):
        self.runtime.cli_unusable.return_value = True

        self.configuration.update_game_config("123", "Test Game", {"multiplier": 2})

        self.runtime.validate_config_content.assert_not_called()
        self.assertTrue(self.configuration.config_file_path.is_file())

    def test_saving_profiles_still_validates_when_cli_usable(self):
        self.runtime.cli_unusable.return_value = False

        self.configuration.update_game_config("123", "Test Game", {"multiplier": 2})

        self.runtime.validate_config_content.assert_called_once()

    def test_reset_game_config_works_without_the_cli(self):
        self.runtime.cli_unusable.return_value = True
        self.configuration.update_game_config("123", "Test Game", {"multiplier": 2})

        result = self.configuration.reset_game_config("123")

        self.assertTrue(result["success"])
        self.runtime.validate_config_content.assert_not_called()

    def _install_payload(self, service, keep_config: bool = False):
        """Materialise the payload files, optionally preserving the config.

        config_file_path is part of the required payload, so writing it blank
        would erase the DLL the caller wants to probe.  keep_config skips it.
        """
        for path in service._required_payload():
            if keep_config and path == service.config_file_path:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("installed", encoding="utf-8")

    def _write_conf(self, service, dll=""):
        service.config_dir.mkdir(parents=True, exist_ok=True)
        service.config_file_path.write_text(
            'version = 2\n\n[global]\n'
            f'dll = {json.dumps(dll)}\nallow_fp16 = true\n',
            encoding="utf-8",
        )

    # --- C1/C2/C12: ARM falls back to a pure-python DLL check -------------

    def test_arm_check_installation_reports_ready_when_dll_is_present(self):
        (self.home / "unused-bin").mkdir(exist_ok=True)
        service, runtime, steam = _build_service(self.home, self.home / "unused-bin")
        dll = self.home / "steamapps/common/Lossless Scaling/lsfg-vk.dll"
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_text("dll", encoding="utf-8")
        steam.find_lsfg_vk_dll.return_value = str(dll)
        self._write_conf(service, str(dll))
        self._install_payload(service)
        with unittest.mock.patch.object(InstallationService, "is_arm_host", return_value=True):
            result = service.check_installation()

        self.assertTrue(result["installed"])
        self.assertTrue(result["lossless_scaling_installed"])
        self.assertIn("x86-64", result["lossless_scaling_status"])
        self.assertNotIn(str(self.home), result["lossless_scaling_status"])

    def test_arm_falls_back_to_steam_discovery_when_conf_has_no_dll(self):
        (self.home / "unused-bin").mkdir(exist_ok=True)
        service, runtime, steam = _build_service(self.home, self.home / "unused-bin")
        dll = self.home / "elsewhere/lsfg-vk.dll"
        dll.parent.mkdir(parents=True, exist_ok=True)
        dll.write_text("dll", encoding="utf-8")
        steam.find_lsfg_vk_dll.return_value = str(dll)
        self._write_conf(service, "")
        self._install_payload(service)
        with unittest.mock.patch.object(InstallationService, "is_arm_host", return_value=True):
            result = service.check_installation()

        self.assertTrue(result["lossless_scaling_installed"])
        steam.find_lsfg_vk_dll.assert_called()

    # --- C13: actionable message without leaking a path ------------------

    def test_arm_check_installation_reports_missing_dll_without_leaking_path(self):
        (self.home / "unused-bin").mkdir(exist_ok=True)
        service, runtime, steam = _build_service(self.home, self.home / "unused-bin")
        steam.find_lsfg_vk_dll.return_value = None
        steam.get_branch_status.return_value = {
            "installed": True,
            "needs_switch": False,
            "target_branch": "lsfg-vk",
            "selected_branch": "lsfg-vk",
            "message": "",
        }
        self._write_conf(service, "")
        self._install_payload(service)
        with unittest.mock.patch.object(InstallationService, "is_arm_host", return_value=True):
            result = service.check_installation()

        self.assertFalse(result["lossless_scaling_installed"])
        self.assertNotIn(str(self.home), result["lossless_scaling_status"])
        self.assertNotIn("None", result["lossless_scaling_status"])

    def test_arm_check_installation_names_wrong_branch(self):
        (self.home / "unused-bin").mkdir(exist_ok=True)
        service, runtime, steam = _build_service(self.home, self.home / "unused-bin")
        steam.find_lsfg_vk_dll.return_value = None
        steam.get_branch_status.return_value = {
            "installed": True,
            "needs_switch": True,
            "target_branch": "lsfg-vk",
            "selected_branch": "public",
            "message": "Select lsfg-vk in Lossless Scaling's Steam Properties > Betas",
        }
        self._write_conf(service, "")
        self._install_payload(service)
        with unittest.mock.patch.object(InstallationService, "is_arm_host", return_value=True):
            result = service.check_installation()

        self.assertFalse(result["lossless_scaling_installed"])
        self.assertIn("lsfg-vk", result["lossless_scaling_status"])

    # --- C14: x86-64 keeps the CLI as the primary source ----------------

    def test_arm_ignores_a_stale_configured_dll_on_the_wrong_branch(self):
        """A DLL left in conf.toml by another branch must not read as ready.

        On x86-64 the CLI catches this.  On ARM the same branch gate has to be
        applied by discovery, or a stale DLL would look perfectly healthy.
        """
        (self.home / "unused-bin").mkdir(exist_ok=True)
        service, runtime, steam = _build_service(self.home, self.home / "unused-bin")
        stale_dll = self.home / "steamapps/common/Lossless Scaling/lsfg-vk.dll"
        stale_dll.parent.mkdir(parents=True, exist_ok=True)
        stale_dll.write_text("dll", encoding="utf-8")
        self._write_conf(service, str(stale_dll))
        self._install_payload(service, keep_config=True)
        # Discovery is branch-gated, so it refuses the stale DLL.
        steam.find_lsfg_vk_dll.return_value = None
        steam.get_branch_status.return_value = {
            "installed": True,
            "needs_switch": True,
            "target_branch": "lsfg-vk",
            "selected_branch": "public",
            "message": "Select lsfg-vk in Lossless Scaling's Steam Properties > Betas",
        }

        with unittest.mock.patch.object(InstallationService, "is_arm_host", return_value=True):
            result = service.check_installation()

        self.assertFalse(result["lossless_scaling_installed"])
        self.assertIn("lsfg-vk", result["lossless_scaling_status"])

    def test_x86_check_installation_still_uses_the_cli(self):
        (self.home / "unused-bin").mkdir(exist_ok=True)
        service, runtime, steam = _build_service(self.home, self.home / "unused-bin")
        runtime.cli_unusable.return_value = False
        runtime.check_lossless_scaling.return_value = {
            "installed": True,
            "status": "Lossless Scaling detected by lsfg-vk",
        }
        self._write_conf(service, "")
        self._install_payload(service)
        with unittest.mock.patch.object(InstallationService, "is_arm_host", return_value=False):
            result = service.check_installation()

        runtime.check_lossless_scaling.assert_called_once()
        self.assertTrue(result["lossless_scaling_installed"])
        steam.find_lsfg_vk_dll.assert_not_called()

    def test_lossless_scaling_status_is_actionable_when_cli_cannot_run(self):
        runtime = RuntimeService(logger=Mock())
        runtime.user_home = self.home
        runtime.cli_unusable = Mock(return_value=True)
        runtime.config_dir = self.home / ".config/lsfg-vk"
        runtime.config_file_path = runtime.config_dir / "conf.toml"
        runtime.config_dir.mkdir(parents=True, exist_ok=True)
        runtime.config_file_path.write_text("version = 2\n", encoding="utf-8")

        status = runtime.check_lossless_scaling()

        self.assertFalse(status["installed"])
        self.assertNotIn(str(self.home), status["status"])
        self.assertIn("x86-64", status["status"])


if __name__ == "__main__":
    unittest.main()
