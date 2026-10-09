from pathlib import Path

LOCAL_BIN = ".local/bin"
LOCAL_SHARE = ".local/share"
LOCAL_LIB = ".local/lib"
VULKAN_LAYER_DIR = ".local/share/vulkan/implicit_layer.d"
CONFIG_DIR = ".config/lsfg-vk"

SCRIPT_NAME = "lsfg"
WRAPPER_FILENAME = ".lsfg"
CONFIG_FILENAME = "conf.toml"
ARCHIVE_FILENAME = "lsfg-vk-2.0.0.tar.xz"
LIB_FILENAME = "liblsfg-vk-layer.so"
LIB_X86_FILENAME = "liblsfg-vk-layer.x86.so"
JSON_FILENAME = "VkLayer_LSFGVK_frame_generation.json"
JSON_X86_FILENAME = "VkLayer_LSFGVK_frame_generation.x86.json"
CLI_FILENAME = "lsfg-vk-cli"
UI_FILENAME = "lsfg-vk-ui"
UI_DESKTOP_FILENAME = "gay.pancake.lsfg-vk-ui.desktop"
UI_ICON_FILENAME = "gay.pancake.lsfg-vk-ui.png"
STEAM_LOSSLESS_SCALING_APP_ID = "993090"
STEAM_LOSSLESS_SCALING_BRANCH = "lsfg-vk"

ARM_ARCHIVE_FILENAME = "lsfg-vk-layer-aarch64.tar.xz"
ARM_LIB_SOURCE_FILENAME = "liblsfg-vk-layer.so"
ARM_MANIFEST_FILENAME = "VkLayer_LSFGVK_frame_generation.json"

ARMADA_DEVICE_ENV = Path("/usr/libexec/armada/device-env")
ARMADA_GAME_LAUNCH = Path("/usr/libexec/armada/armada-game-launch")

ELF_MACHINE_AARCH64 = 183

# lsfg-vk v1 layer files.  These predate this change set (they arrived with
# the v2 runtime migration) and stay wired into install/uninstall so that a
# user upgrading from v0.12.x does not keep a stale v1 Vulkan layer, which
# would load alongside the v2 layer and break frame generation.  They are not
# the ARM payload: the ARM names above are distinct and carry no "legacy"
# meaning.  Covered by tests/test_installation_cleanup.py.
LEGACY_LIB_FILENAME = "liblsfg-vk.so"
LEGACY_JSON_FILENAME = "VkLayer_LS_frame_generation.json"

BIN_DIR = "bin"
