import { useState } from "react";
import { ButtonItem, DialogButton, ModalRoot, PanelSectionRow, SliderField, TextField, ToggleField, showModal } from "@decky/ui";
import { ConfigurationData, FLOW_SCALE, PERFORMANCE_MODE, OVERRIDE_PRESENT_MODE, PRESERVE_SWAPCHAIN_IMAGE_COUNT } from "../config/configSchema";

interface ConfigurationSectionProps {
  config: ConfigurationData;
  onConfigChange: (fieldName: keyof ConfigurationData, value: boolean | number | string | string[]) => Promise<void>;
}

function parseNames(raw: string): string[] {
  // Accept one per line, or comma separated, and drop blank entries.
  return raw
    .split(/[\n,]/)
    .map((entry) => entry.trim())
    .filter(Boolean);
}

interface ExecutableNamesFieldProps {
  activeIn: string[];
  appid?: string;
  onConfigChange: ConfigurationSectionProps["onConfigChange"];
}

/** The dialog itself, so React state lives in a component and not a callback. */
function ExecutableNamesModal({
  initial,
  onSave,
  onCancel,
}: {
  initial: string;
  onSave: (names: string[]) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(initial);

  return (
    <ModalRoot bAllowFullSize closeModal={onCancel} onCancel={onCancel}>
      <div style={{ padding: "8px 16px 16px", display: "grid", gap: 10 }}>
        <div style={{ fontSize: 16, fontWeight: 600 }}>Executable names</div>
        <div style={{ opacity: 0.75, fontSize: 12, lineHeight: 1.4 }}>
          Required on some ARM handhelds, where the Vulkan layer cannot match a
          Steam App ID. Add the game&apos;s executable, one per line.
        </div>
        {/* TextField is the component that raises the Steam keyboard; a raw
            <textarea> is inert on a gamepad. */}
        <TextField
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <DialogButton onClick={() => onSave(parseNames(draft))}>Save</DialogButton>
        <DialogButton onClick={onCancel}>Cancel</DialogButton>
      </div>
    </ModalRoot>
  );
}

function ExecutableNamesField({
  activeIn,
  appid,
  onConfigChange,
}: ExecutableNamesFieldProps) {
  // The Steam App ID is managed by the plugin and must stay untouched, so it
  // is filtered out of what the user edits.
  const names = activeIn.filter((entry) => entry !== appid);

  // A modal rather than an inline input: nothing focusable is added to the
  // scrolling panel, which the gamepad navigator handles poorly, and the
  // Steam keyboard opens reliably inside a dialog.
  const openEditor = () => {
    const modal = showModal(
      <ExecutableNamesModal
        initial={names.join("\n")}
        onSave={(next) => {
          void onConfigChange("active_in" as keyof ConfigurationData, next);
          modal.Close();
        }}
        onCancel={() => modal.Close()}
      />,
      undefined,
      {
        strTitle: "Executable names",
        bNeverPopOut: true,
        popupWidth: 620,
        popupHeight: 460,
      },
    );
  };

  return (
    <PanelSectionRow>
      <ButtonItem layout="below" onClick={openEditor}>
        {names.length > 0 ? `Executable names (${names.length})` : "Set executable name"}
      </ButtonItem>
    </PanelSectionRow>
  );
}

export function ConfigurationSection({ config, onConfigChange }: ConfigurationSectionProps) {
  return <>
    <PanelSectionRow>
      <SliderField label={`Flow Scale (${Math.round(config.flow_scale * 100)}%)`} value={config.flow_scale} min={0.25} max={1} step={0.01} onChange={(value) => onConfigChange(FLOW_SCALE, value)} />
    </PanelSectionRow>
    <PanelSectionRow>
      <ToggleField label="Performance Mode" checked={config.performance_mode} onChange={(value) => onConfigChange(PERFORMANCE_MODE, value)} />
    </PanelSectionRow>
    <PanelSectionRow>
      <ToggleField label="Present Mode Override" checked={config.override_present_mode} onChange={(value) => onConfigChange(OVERRIDE_PRESENT_MODE, value)} />
    </PanelSectionRow>
    <PanelSectionRow>
      <ToggleField label="Preserve Swapchain Image Count" checked={config.preserve_swapchain_image_count} onChange={(value) => onConfigChange(PRESERVE_SWAPCHAIN_IMAGE_COUNT, value)} />
    </PanelSectionRow>
    <ExecutableNamesField
      activeIn={config.active_in ?? []}
      onConfigChange={onConfigChange}
    />
  </>;
}
