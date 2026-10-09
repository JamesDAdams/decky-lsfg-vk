import subprocess
import sys
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

from lsfg_vk.wrapper_service import WrapperService


class WrapperServiceTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name) / "home" / "deck"
        self.home.mkdir(parents=True)
        self.service = WrapperService()
        self.service.user_home = self.home
        self.service.config_dir = self.home / ".config/lsfg-vk"
        self.service.config_file_path = self.service.config_dir / "conf.toml"
        self.service.sidecar_path = self.service.config_dir / "workarounds.json"
        self.service.wrapper_path = self.home / ".lsfg"

    def tearDown(self):
        self.tempdir.cleanup()

    def _state(self, **changes):
        state = self.service.default_state()
        state.update(changes)
        return state

    def _run(self, appid, *args, env=None):
        process_env = {"PATH": "/usr/bin:/bin", "SteamAppId": str(appid)}
        if env:
            process_env.update(env)
        return subprocess.run(
            [str(self.service.wrapper_path), *args],
            env=process_env,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_writes_owned_dispatcher_and_validates_shell(self):
        response = self.service.set("123", self._state(dxvkFrameRate=60, enableZink=True))
        self.assertTrue(response["success"])
        self.assertEqual(response["wrapper_path"], "~/.lsfg")
        self.assertTrue(response["wrapper_owned"])
        self.assertEqual(response["state"]["dxvkFrameRate"], 60)
        self.assertEqual(subprocess.run(["/bin/sh", "-n", str(self.service.wrapper_path)]).returncode, 0)
        self.assertIn(self.service.MARKER, self.service.wrapper_path.read_text(encoding="utf-8"))
        self.assertEqual(self.service.get("123")["state"], self._state(dxvkFrameRate=60, enableZink=True))

    def test_dispatch_exports_appid_config_and_workarounds(self):
        self.service.set(
            "123",
            self._state(dxvkFrameRate=30, disableSteamdeckMode=True, disableVkbasalt=True, enableZink=True),
        )
        result = self._run(
            123,
            "/usr/bin/env",
            env={
                "DXVK_CONFIG": "dxgi.syncInterval = 0",
                "DXVK_FRAME_RATE": "5",
                "ENABLE_GAMESCOPE_WSI": "1",
                "DISABLE_LSFGVK": "1",
                "DISABLE_LSFG": "1",
                "DISABLE_VKBASALT": "0",
                "MESA_LOADER_DRIVER_OVERRIDE": "llvmpipe",
                "MANGOHUD": "1",
            },
        )
        values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        self.assertEqual(values["SteamAppId"], "123")
        self.assertEqual(values["LSFGVK_CONFIG"], str(self.service.config_file_path))
        self.assertEqual(values["ENABLE_GAMESCOPE_WSI"], "0")
        self.assertEqual(values["DXVK_HDR"], "0")
        self.assertEqual(values["SteamDeck"], "0")
        self.assertEqual(values["DISABLE_VKBASALT"], "1")
        self.assertEqual(values["__GLX_VENDOR_LIBRARY_NAME"], "mesa")
        self.assertEqual(values["MESA_LOADER_DRIVER_OVERRIDE"], "zink")
        self.assertEqual(values["GALLIUM_DRIVER"], "zink")
        self.assertEqual(values["DXVK_CONFIG"], "dxgi.syncInterval = 0; dxvk.maxFrameRate = 30")
        self.assertEqual(values["MANGOHUD"], "1")
        self.assertNotIn("DXVK_FRAME_RATE", values)
        self.assertNotIn("ENABLE_VKBASALT", values)
        self.assertNotIn("DISABLE_LSFGVK", values)
        self.assertNotIn("DISABLE_LSFG", values)

    def test_preserves_steamdeck_by_default_and_disables_when_requested(self):
        self.service.set("123", self._state())
        inherited = self._run(123, "/usr/bin/env", env={"SteamDeck": "1"})
        inherited_values = dict(line.split("=", 1) for line in inherited.stdout.splitlines() if "=" in line)
        self.assertEqual(inherited_values["SteamDeck"], "1")

        self.service.set("123", self._state(disableSteamdeckMode=True))
        disabled = self._run(123, "/usr/bin/env", env={"SteamDeck": "1"})
        disabled_values = dict(line.split("=", 1) for line in disabled.stdout.splitlines() if "=" in line)
        self.assertEqual(disabled_values["SteamDeck"], "0")

    def test_wrapper_is_transport_agnostic(self):
        self.service.set("123", self._state())
        fake = self.home / "target"
        fake.write_text("#!/bin/sh\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
        fake.chmod(0o755)
        result = self._run(123, str(fake), "run", "org.example.Game")
        self.assertEqual(result.stdout.splitlines(), ["run", "org.example.Game"])
        content = self.service.wrapper_path.read_text(encoding="utf-8")
        self.assertNotIn("flatpakAppId", content)
        self.assertNotIn("shortcut_exe", content)
        self.assertNotIn("--filesystem", content)

    def test_appid_fallback_and_unmatched_passthrough(self):
        self.service.set("123", self._state(disableGamescopeWsi=False, disableHdr=False))
        self.service.set("456", self._state(disableSteamdeckMode=True))
        fallback = subprocess.run(
            [str(self.service.wrapper_path), "/usr/bin/env"],
            env={"PATH": "/usr/bin:/bin", "SteamAppId": "bad", "SteamGameId": "456"},
            capture_output=True,
            text=True,
            check=True,
        )
        fallback_values = dict(line.split("=", 1) for line in fallback.stdout.splitlines() if "=" in line)
        self.assertEqual(fallback_values["SteamDeck"], "0")
        self.assertEqual(fallback_values["SteamAppId"], "456")

        passthrough = subprocess.run(
            [str(self.service.wrapper_path), "/usr/bin/env"],
            env={"PATH": "/usr/bin:/bin", "SteamAppId": "999", "KEEP": "yes", "DXVK_HDR": "1"},
            capture_output=True,
            text=True,
            check=True,
        )
        passthrough_values = dict(line.split("=", 1) for line in passthrough.stdout.splitlines() if "=" in line)
        self.assertEqual(passthrough_values["KEEP"], "yes")
        self.assertEqual(passthrough_values["DXVK_HDR"], "1")

    def _write_empty_dispatcher(self):
        """Write the wrapper for a game that has no workaround entry."""
        self.service.config_dir.mkdir(parents=True, exist_ok=True)
        self.service.sidecar_path.write_text(
            '{"version": 2, "apps": {}}', encoding="utf-8"
        )
        self.service.wrapper_path.write_text(
            self.service._render_wrapper({"version": 2, "apps": {}})
        )
        self.service.wrapper_path.chmod(0o755)
        self.assertEqual(
            subprocess.run(["/bin/sh", "-n", str(self.service.wrapper_path)]).returncode,
            0,
        )

    def _run_wrapper(self, appid, *args, env=None):
        process_env = {"PATH": "/usr/bin:/bin"}
        if appid is not None:
            process_env["SteamAppId"] = str(appid)
        if env:
            process_env.update(env)
        return subprocess.run(["/usr/bin/env", "-i", *(
            [f"{key}={value}" for key, value in process_env.items()]
        ), str(self.service.wrapper_path), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    @staticmethod
    def _as_values(result):
        return dict(
            line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
        )

    def test_config_is_exported_even_without_a_workaround_entry(self):
        """A plain frame-generation profile must still reach the game.

        The Vulkan layer reads its configuration exclusively through
        LSFGVK_CONFIG, so a game that has no workaround entry used to run
        without any configuration and silently ignored the multiplier.
        """
        self._write_empty_dispatcher()

        values = self._as_values(self._run_wrapper("1091500", "/usr/bin/env"))

        self.assertIn("LSFGVK_CONFIG", values)
        self.assertEqual(values["LSFGVK_CONFIG"], str(self.service.config_file_path))
        self.assertEqual(values["SteamAppId"], "1091500")

    def test_config_reaches_a_game_that_has_a_workaround_entry(self):
        """Matched and unmatched games both need the configuration."""
        self.service.set("1091500", self._state(dxvkFrameRate=30))

        values = self._as_values(self._run_wrapper("1091500", "/usr/bin/env"))

        self.assertIn("LSFGVK_CONFIG", values)
        self.assertEqual(values["LSFGVK_CONFIG"], str(self.service.config_file_path))
        # Per-game workarounds still apply on the matched path.
        self.assertIn("dxvk.maxFrameRate = 30", values["DXVK_CONFIG"])
        # MANAGED_ENV_KEYS stay per-game, so they are not exported globally.
        self.assertEqual(values["ENABLE_GAMESCOPE_WSI"], "0")
        self.assertEqual(values["DXVK_HDR"], "0")

    def test_unmanaged_env_survives_for_a_game_without_workarounds(self):
        self._write_empty_dispatcher()

        values = self._as_values(
            self._run_wrapper("999", "/usr/bin/env", env={"KEEP": "yes"})
        )

        self.assertEqual(values["KEEP"], "yes")
        self.assertIn("LSFGVK_CONFIG", values)

    def test_removing_the_last_workaround_keeps_the_config_exported(self):
        """The regression users actually hit: configure then clear workarounds.

        Removing the last workaround leaves an empty app map, which used to
        produce a wrapper that no longer exported LSFGVK_CONFIG at all.
        """
        self.service.set("1091500", self._state())
        self.service.remove("1091500")

        self.assertIn(self.service.MARKER, self.service.wrapper_path.read_text())
        values = self._as_values(self._run_wrapper("1091500", "/usr/bin/env"))

        self.assertIn("LSFGVK_CONFIG", values)
        self.assertEqual(values["LSFGVK_CONFIG"], str(self.service.config_file_path))
        # Workaround state is gone, so nothing per-game leaks into the env.
        self.assertNotIn("ENABLE_GAMESCOPE_WSI", values)

    def test_empty_appid_is_not_published_to_non_steam_launches(self):
        """A non-Steam shortcut must not gain a set-but-empty SteamAppId.

        A launch that distinguishes unset from empty would otherwise behave
        differently than before the configuration export was made
        unconditional.
        """
        self._write_empty_dispatcher()

        values = self._as_values(self._run_wrapper(None, "/usr/bin/env"))

        self.assertNotIn("SteamAppId", values)
        self.assertIn("LSFGVK_CONFIG", values)

    def test_garbage_appid_stays_untouched(self):
        """A non-numeric SteamAppId resolves to nothing, so it is left alone."""
        self._write_empty_dispatcher()

        values = self._as_values(self._run_wrapper("not-a-number", "/usr/bin/env"))

        self.assertEqual(values["SteamAppId"], "not-a-number")
        self.assertIn("LSFGVK_CONFIG", values)

    def test_invalid_state_and_foreign_wrapper_fail_closed(self):
        invalid = self.service.set("0", self.service.default_state())
        self.assertFalse(invalid["success"])
        invalid = self.service.set("123", {**self.service.default_state(), "dxvkFrameRate": 61})
        self.assertFalse(invalid["success"])

        self.service.wrapper_path.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
        response = self.service.set("123", self.service.default_state())
        self.assertFalse(response["success"])
        self.assertIn("unowned", response["error"])
        self.assertEqual(self.service.wrapper_path.read_text(encoding="utf-8"), "#!/bin/sh\necho foreign\n")

    def test_remove_keeps_safe_passthrough_wrapper(self):
        self.service.set("123", self.service.default_state())
        response = self.service.remove("123")
        self.assertTrue(response["success"])
        self.assertIsNone(self.service.get("123")["state"])
        self.assertTrue(self.service.wrapper_path.exists())
        result = subprocess.run(
            [str(self.service.wrapper_path), "/usr/bin/printf", "ok"],
            env={"PATH": "/usr/bin:/bin", "SteamAppId": "123"},
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.stdout, "ok")

    def test_purge_removes_owned_wrapper_and_state(self):
        self.service.set("123", self._state(), non_steam=True)
        self.assertTrue(self.service.get("123")["non_steam"])
        self.assertEqual(self.service.list_apps()["apps"][0]["non_steam"], True)
        response = self.service.purge()
        self.assertTrue(response["success"])
        self.assertEqual(response["removed_files"], [str(self.service.wrapper_path), str(self.service.sidecar_path)])
        self.assertFalse(self.service.wrapper_path.exists())
        self.assertFalse(self.service.sidecar_path.exists())

    def test_purge_refuses_foreign_wrapper(self):
        self.service.wrapper_path.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
        response = self.service.purge()
        self.assertFalse(response["success"])
        self.assertIn("unowned", response["error"])
        self.assertTrue(self.service.wrapper_path.exists())

    def test_purge_refuses_invalid_state(self):
        self.service.config_dir.mkdir(parents=True, exist_ok=True)
        self.service.sidecar_path.write_text("not json", encoding="utf-8")
        self.service.wrapper_path.write_text(
            f"#!/bin/sh\n{self.service.MARKER}\nexec \"$@\"\n",
            encoding="utf-8",
        )
        response = self.service.purge()
        self.assertFalse(response["success"])
        self.assertTrue(self.service.wrapper_path.exists())
        self.assertTrue(self.service.sidecar_path.exists())

    def test_neutralize_leaves_dependency_free_passthrough_wrapper(self):
        self.service.set("123", self._state())
        response = self.service.neutralize()
        self.assertTrue(response["success"])
        self.assertFalse(self.service.sidecar_path.exists())
        self.assertTrue(self.service.wrapper_path.exists())
        content = self.service.wrapper_path.read_text(encoding="utf-8")
        self.assertIn(self.service.MARKER, content)
        self.assertNotIn("LSFGVK_CONFIG", content)
        self.assertEqual(self._run(123, "/usr/bin/printf", "ok").stdout, "ok")

    def test_neutralize_refuses_foreign_wrapper(self):
        self.service.wrapper_path.write_text("#!/bin/sh\necho foreign\n", encoding="utf-8")
        response = self.service.neutralize()
        self.assertFalse(response["success"])
        self.assertIn("unowned", response["error"])
        self.assertEqual(self.service.wrapper_path.read_text(encoding="utf-8"), "#!/bin/sh\necho foreign\n")


if __name__ == "__main__":
    unittest.main()
