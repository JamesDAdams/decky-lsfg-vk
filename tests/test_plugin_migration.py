import asyncio
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock


class PluginMigrationTests(unittest.TestCase):
    def _load_plugin(self):
        decky = types.SimpleNamespace(
            DECKY_HOME="/decky",
            DECKY_USER_HOME="/home/deck",
            migrate_logs=Mock(),
            migrate_settings=Mock(),
            migrate_runtime=Mock(),
            logger=Mock(),
        )
        previous_decky = sys.modules.get("decky")
        previous_tomllib = sys.modules.get("tomllib")
        previous_plugin = sys.modules.pop("lsfg_vk.plugin", None)
        sys.modules["decky"] = decky
        sys.modules["tomllib"] = types.SimpleNamespace(loads=Mock())
        sys.path.insert(0, "py_modules")
        from lsfg_vk.plugin import Plugin
        return Plugin, decky, previous_decky, previous_tomllib, previous_plugin

    def _restore(self, previous_decky, previous_tomllib, previous_plugin):
        sys.path.remove("py_modules")
        if previous_decky is None:
            sys.modules.pop("decky", None)
        else:
            sys.modules["decky"] = previous_decky
        if previous_tomllib is None:
            sys.modules.pop("tomllib", None)
        else:
            sys.modules["tomllib"] = previous_tomllib
        if previous_plugin is None:
            sys.modules.pop("lsfg_vk.plugin", None)
        else:
            sys.modules["lsfg_vk.plugin"] = previous_plugin

    def test_migration_only_runs_decky_path_migrations(self):
        Plugin, decky, previous_decky, previous_tomllib, previous_plugin = self._load_plugin()
        try:
            plugin = Plugin.__new__(Plugin)
            plugin.installation_service = Mock()
            plugin.flatpak_service = Mock()

            asyncio.run(plugin._migration())

            decky.migrate_logs.assert_called_once()
            decky.migrate_settings.assert_called_once()
            decky.migrate_runtime.assert_called_once()
            plugin.installation_service.install.assert_not_called()
            plugin.flatpak_service.prepare_app.assert_not_called()
        finally:
            self._restore(previous_decky, previous_tomllib, previous_plugin)

    def test_uninstall_cleans_owned_flatpak_state_and_profiles(self):
        Plugin, _decky, previous_decky, previous_tomllib, previous_plugin = self._load_plugin()
        try:
            plugin = Plugin.__new__(Plugin)
            plugin.installation_service = Mock()
            plugin.flatpak_service = Mock()
            plugin.configuration_service = Mock()
            plugin.wrapper_service = Mock()
            plugin.flatpak_service.remove_plugin_owned_environment.return_value = {"success": True}
            plugin.configuration_service.reset_all_flatpak_configs.return_value = {"success": True}
            plugin.wrapper_service.neutralize.return_value = {"success": True}

            asyncio.run(plugin._uninstall())

            plugin.flatpak_service.remove_plugin_owned_environment.assert_called_once_with()
            plugin.configuration_service.reset_all_flatpak_configs.assert_called_once_with()
            plugin.wrapper_service.neutralize.assert_called_once_with()
            plugin.wrapper_service.purge.assert_not_called()
            plugin.installation_service.cleanup_on_uninstall.assert_called_once_with()
        finally:
            self._restore(previous_decky, previous_tomllib, previous_plugin)

    def test_uninstall_preserves_profiles_when_flatpak_cleanup_fails(self):
        Plugin, _decky, previous_decky, previous_tomllib, previous_plugin = self._load_plugin()
        try:
            plugin = Plugin.__new__(Plugin)
            plugin.installation_service = Mock()
            plugin.flatpak_service = Mock()
            plugin.configuration_service = Mock()
            plugin.wrapper_service = Mock()
            plugin.flatpak_service.remove_plugin_owned_environment.return_value = {"success": False, "error": "changed"}

            asyncio.run(plugin._uninstall())

            plugin.configuration_service.reset_all_flatpak_configs.assert_not_called()
            plugin.wrapper_service.purge.assert_not_called()
            plugin.installation_service.cleanup_on_uninstall.assert_not_called()
        finally:
            self._restore(previous_decky, previous_tomllib, previous_plugin)

    def test_write_debug_report_includes_diagnostics_and_files(self):
        Plugin, decky, previous_decky, previous_tomllib, previous_plugin = self._load_plugin()
        try:
            plugin = Plugin.__new__(Plugin)
            plugin.installation_service = Mock()
            plugin.configuration_service = Mock()
            plugin.wrapper_service = Mock()
            plugin.flatpak_service = Mock()
            plugin.installation_service.is_arm_host.return_value = False
            plugin.installation_service.lib_file = None
            plugin.installation_service.lib_x86_file = None
            plugin.installation_service.json_file = None
            plugin.installation_service.json_x86_file = None
            plugin.installation_service.cli_file = None
            plugin.runtime_service = Mock()
            plugin.runtime_service.cli_unusable.return_value = False
            plugin.runtime_service.check_lossless_scaling.return_value = {
                "installed": True,
                "status": "Lossless Scaling detected by lsfg-vk",
            }
            plugin.steam_service = Mock()
            plugin.steam_service.get_branch_status.return_value = {
                "installed": True,
                "needs_switch": False,
            }
            plugin.configuration_service.config_file_path = Path("/nonexistent/conf.toml")
            plugin.wrapper_service.wrapper_path = Path("/nonexistent/.lsfg")
            plugin.wrapper_service.sidecar_path = Path("/nonexistent/workarounds.json")
            plugin.flatpak_service.ownership_path = None

            # The report must land somewhere the user can reach: Decky runs as
            # a system service, so /tmp is often a private namespace.
            with tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary) / "home" / "deck"
                (home / "Downloads").mkdir(parents=True)
                plugin.installation_service.user_home = home

                result = asyncio.run(plugin.write_debug_report())

                self.assertTrue(result["success"])
                self.assertTrue(result["report"])
                self.assertEqual(result["location"], "Downloads")
                self.assertEqual(Path(result["path"]).parent, home / "Downloads")
                self.assertTrue(Path(result["path"]).is_file())
                # The facts that explain an inert multiplier must be present.
                for marker in ("host_is_arm", "cli_usable", "wrapper_exports_config",
                               "lossless_scaling", "branch_status", "plugin_version"):
                    self.assertIn(marker, result["report"])

            # Without a Downloads directory the home directory is the fallback.
            with tempfile.TemporaryDirectory() as temporary:
                home = Path(temporary) / "home" / "deck"
                home.mkdir(parents=True)
                plugin.installation_service.user_home = home

                result = asyncio.run(plugin.write_debug_report())

                self.assertTrue(result["success"])
                self.assertEqual(result["location"], "home")
                self.assertTrue(Path(result["path"]).is_file())
        finally:
            self._restore(previous_decky, previous_tomllib, previous_plugin)


if __name__ == "__main__":
    unittest.main()
