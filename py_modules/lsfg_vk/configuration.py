import re
from typing import Any, Dict

from .base_service import BaseService
from .config_schema import ConfigurationManager, ProfileData, UnsupportedConfigurationVersion
from .runtime_service import RuntimeService


class ConfigurationService(BaseService):
    FLATPAK_PROFILE_PREFIX = "flatpak:"

    def __init__(self, logger=None, runtime_service: RuntimeService = None):
        super().__init__(logger)
        self.runtime_service = runtime_service or RuntimeService(logger=self.log)

    def _default_data(self) -> ProfileData:
        return {"profiles": {}, "global_config": {"dll": "", "no_fp16": False}}

    def _reset_legacy_config(self, version: Any) -> ProfileData:
        data = self._default_data()
        self._remove_if_exists(self.config_file_path)
        content = ConfigurationManager.generate_toml_content_multi_profile(data)
        self._write_file(self.config_file_path, content)
        self.log.warning(
            f"Removed legacy lsfg-vk configuration version {version!r}; reset conf.toml to v2 defaults"
        )
        return data

    def _get_profile_data(self) -> ProfileData:
        if not self.config_file_path.exists():
            return self._default_data()
        content = self.config_file_path.read_text(encoding="utf-8")
        try:
            return ConfigurationManager.parse_toml_content_multi_profile(content)
        except UnsupportedConfigurationVersion as error:
            if not ConfigurationManager.is_discardable_legacy_version(error.version):
                raise
            return self._reset_legacy_config(error.version)

    def _save_profile_data(self, data: ProfileData) -> None:
        content = ConfigurationManager.generate_toml_content_multi_profile(data)
        if self.runtime_service.cli_unusable():
            self.log.warning(
                f"Skipping lsfg-vk CLI validation because {self.runtime_service.CLI_UNSUPPORTED_STATUS}"
            )
        else:
            self.runtime_service.validate_config_content(content)
        self._write_file(self.config_file_path, content, 0o644)

    @staticmethod
    def _profile_name(data: ProfileData, appid: str, game_name: str) -> str:
        name = str(game_name).strip()
        if not name:
            raise ValueError("game name is required")
        existing = data["profiles"].get(name)
        if existing is not None and str(appid) not in existing.get("active_in", []):
            name = f"{name} ({appid})"
        return name

    @staticmethod
    def _profile_for_appid(data: ProfileData, appid: str):
        return next(
            (
                (name, profile)
                for name, profile in data["profiles"].items()
                if str(appid) in profile.get("active_in", [])
            ),
            (None, None),
        )

    @classmethod
    def flatpak_profile_name(cls, app_id: str) -> str:
        value = str(app_id).strip()
        if not value:
            raise ValueError("Flatpak application ID is required")
        return f"{cls.FLATPAK_PROFILE_PREFIX}{value}"

    @staticmethod
    def _public_config(config: Dict[str, Any]) -> Dict[str, Any]:
        return ConfigurationManager.validate_config(config)

    def get_game_configs(self) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            games = []
            for name, raw in data["profiles"].items():
                active_in = raw.get("active_in", [])
                appids = [
                    entry
                    for entry in active_in
                    if re.fullmatch(r"-?[0-9]+", str(entry))
                ]
                # The App ID stays first, but a profile may also carry
                # executable names so the layer can match it on hosts without
                # App ID support.  Requiring exactly one entry would hide
                # those games, and the UI would then fall back to defaults
                # and overwrite the saved configuration.
                if len(appids) != 1:
                    continue
                games.append({"appid": str(appids[0]), "profile": name, "config": self._public_config(raw)})
            return self._success_response(dict, global_config=dict(data["global_config"]), games=games)
        except Exception as error:
            self.log.error(f"Error reading game configs: {error}")
            return self._error_response(dict, str(error), games=[])

    def update_global_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            merged_config = {**data["global_config"], **config}
            validated = self._public_config(merged_config)
            data["global_config"] = {
                "dll": validated["dll"],
                "no_fp16": validated["no_fp16"],
            }
            self._save_profile_data(data)
            return self._success_response(dict, global_config=dict(data["global_config"]))
        except Exception as error:
            return self._error_response(dict, str(error), global_config=None)

    @staticmethod
    def _active_in_entries(config: Dict[str, Any], appid: str) -> list[str]:
        """Build the active_in list for a Steam game profile.

        The Steam App ID is always kept first, because the official layer
        matches it.  The Android-based ARM layer has no App ID matching at
        all, so any executable names the user supplied are appended to give
        the layer a way to reach the profile there.  Harmless on x86-64.
        """
        requested = config.get("active_in")
        if requested in (None, ""):
            requested = []
        if isinstance(requested, str):
            requested = [requested]
        entries = [appid]
        for item in requested or []:
            entry = str(item).strip()
            if entry and entry not in entries:
                entries.append(entry)
        return entries

    def update_game_config(self, appid: str, game_name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            old_name, _ = self._profile_for_appid(data, appid)
            name = self._profile_name(data, appid, game_name)
            merged_config = {**data["global_config"], **{key: value for key, value in config.items() if key != "no_fp16"}}
            if not config.get("dll"):
                merged_config["dll"] = data["global_config"].get("dll", "")
            validated = self._public_config(merged_config)
            validated["active_in"] = self._active_in_entries(config, str(appid))
            if old_name and old_name != name:
                data["profiles"].pop(old_name, None)
            data["profiles"][name] = validated
            self._save_profile_data(data)
            return self._success_response(dict, appid=str(appid), config=validated)
        except Exception as error:
            return self._error_response(dict, str(error), appid=str(appid), config=None)

    def get_flatpak_config(self, app_id: str) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            name = self.flatpak_profile_name(app_id)
            raw = data["profiles"].get(name)
            return self._success_response(
                dict,
                app_id=str(app_id),
                profile=name,
                exists=raw is not None,
                config=self._public_config(raw) if raw is not None else None,
                global_config=dict(data["global_config"]),
            )
        except Exception as error:
            return self._error_response(dict, str(error), app_id=str(app_id), config=None, exists=False)

    def update_flatpak_config(self, app_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            name = self.flatpak_profile_name(app_id)
            merged_config = {**data["global_config"], **{key: value for key, value in config.items() if key != "no_fp16"}}
            if not config.get("dll"):
                merged_config["dll"] = data["global_config"].get("dll", "")
            validated = self._public_config(merged_config)
            validated["active_in"] = []
            data["profiles"][name] = validated
            self._save_profile_data(data)
            return self._success_response(dict, app_id=str(app_id), profile=name, exists=True, config=validated)
        except Exception as error:
            return self._error_response(dict, str(error), app_id=str(app_id), config=None, exists=False)

    def reset_flatpak_config(self, app_id: str) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            name = self.flatpak_profile_name(app_id)
            data["profiles"].pop(name, None)
            self._save_profile_data(data)
            return self._success_response(dict, app_id=str(app_id), profile=name, exists=False)
        except Exception as error:
            return self._error_response(dict, str(error), app_id=str(app_id), config=None, exists=False)

    def reset_all_flatpak_configs(self) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            data["profiles"] = {
                name: profile
                for name, profile in data["profiles"].items()
                if not name.startswith(self.FLATPAK_PROFILE_PREFIX)
            }
            self._save_profile_data(data)
            return self._success_response(dict, global_config=dict(data["global_config"]))
        except Exception as error:
            return self._error_response(dict, str(error))

    def reset_game_config(self, appid: str) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            name, _ = self._profile_for_appid(data, appid)
            if name:
                data["profiles"].pop(name, None)
            self._save_profile_data(data)
            return self._success_response(dict, appid=str(appid), exists=False)
        except Exception as error:
            return self._error_response(dict, str(error), appid=str(appid), config=None)

    def reset_game_configs(self, appids: list[str]) -> Dict[str, Any]:
        try:
            if not isinstance(appids, list):
                raise ValueError("appids must be a list")
            requested = set()
            for appid in appids:
                if isinstance(appid, bool) or not isinstance(appid, (str, int)):
                    raise ValueError("appids must contain only strings or integers")
                value = str(appid)
                if not re.fullmatch(r"-?[0-9]+", value):
                    raise ValueError("appids must contain only numeric App IDs")
                requested.add(value)

            data = self._get_profile_data()
            data["profiles"] = {
                name: profile
                for name, profile in data["profiles"].items()
                if not (
                    len(profile.get("active_in", [])) == 1
                    and str(profile["active_in"][0]) in requested
                )
            }
            self._save_profile_data(data)
            return self._success_response(dict, global_config=dict(data["global_config"]), games=[])
        except Exception as error:
            return self._error_response(dict, str(error), games=[])

    def reset_all_game_configs(self) -> Dict[str, Any]:
        try:
            data = self._get_profile_data()
            data["profiles"] = {
                name: profile
                for name, profile in data["profiles"].items()
                if not (
                    len(profile.get("active_in", [])) == 1
                    and re.fullmatch(r"-?[0-9]+", str(profile.get("active_in", [""])[0]))
                )
            }
            self._save_profile_data(data)
            return self._success_response(dict, global_config=dict(data["global_config"]), games=[])
        except Exception as error:
            return self._error_response(dict, str(error), games=[])
