import { PanelSectionRow, SliderField, ToggleField } from "@decky/ui";
import { useState } from "react";
import { ConfigurationData, FLOW_SCALE, PERFORMANCE_MODE, OVERRIDE_PRESENT_MODE, PRESERVE_SWAPCHAIN_IMAGE_COUNT } from "../config/configSchema";

interface ConfigurationSectionProps {
  config: ConfigurationData;
  onConfigChange: (fieldName: keyof ConfigurationData, value: boolean | number | string | string[]) => Promise<void>;
}

function ExecutableNamesField({
  activeIn,
  appid,
  onConfigChange,
}: {
  activeIn: string[];
  appid?: string;
  onConfigChange: ConfigurationSectionProps["onConfigChange"];
}) {
  const [value, setValue] = useState("");
  // The Steam App ID is managed by the plugin and must stay untouched, so it
  // is filtered out of what the user edits.
  const names = activeIn.filter((entry) => entry !== appid);

  const commit = (next: string[]) => {
    void onConfigChange("active_in" as keyof ConfigurationData, next);
  };

  return (
    <PanelSectionRow>
      <div style={{ padding: "6px 16px 10px 16px", width: "100%", boxSizing: "border-box" }}>
        <div style={{ marginBottom: 4 }}>Executable names</div>
        <div style={{ opacity: 0.75, fontSize: 12, lineHeight: 1.4 }}>
          Required on some ARM handhelds, where the Vulkan layer cannot match a
          Steam App ID. Add the game&apos;s executable, e.g.{" "}
          <code>Game.exe</code>. One per line.
        </div>
        <textarea
          value={value}
          placeholder={names.length ? names.join("\n") : "Game.exe"}
          onChange={(event) => setValue(event.target.value)}
          onBlur={() => {
            const parsed = value
              .split(/[\n,]/)
              .map((entry) => entry.trim())
              .filter(Boolean);
            commit(parsed);
            setValue("");
          }}
          rows={2}
          style={{
            width: "100%",
            marginTop: 6,
            boxSizing: "border-box",
            padding: 6,
            resize: "vertical",
          }}
        />
        {names.length > 0 && (
          <div style={{ marginTop: 6, fontSize: 12, opacity: 0.75 }}>
            Currently: {names.join(", ")}
          </div>
        )}
      </div>
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
