import { ButtonItem, Field, PanelSection, PanelSectionRow, Spinner } from "@decky/ui";
import { useEffect, useState } from "react";
import { RiArrowDownSFill, RiArrowUpSFill } from "react-icons/ri";
import { getDebugFileContents, writeDebugReport, type DebugFileContent, type DebugFileContentsResult } from "../api/lsfgApi";
import { showErrorToast, showSuccessToast } from "../utils/toastUtils";
import t from "../i18n/i18n";

function usePersistentCollapsed(key: string) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(key) !== "false";
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(key, String(collapsed));
    } catch {
      // Persisting the view preference is optional.
    }
  }, [collapsed, key]);

  return [collapsed, () => setCollapsed((value) => !value)] as const;
}

function DebugFileSection({ file }: { file: DebugFileContent }) {
  const [collapsed, toggleCollapsed] = usePersistentCollapsed(`lsfg-debug-file-${file.id}-collapsed-v1`);
  const status = file.exists ? "Present" : "Not present";

  return (
    <>
      <PanelSectionRow>
        <Field
          label={file.label}
          description={`${file.path} · ${status}`}
          bottomSeparator="none"
        />
      </PanelSectionRow>
      <PanelSectionRow>
        <div
          className="LSFG_DebugFileCollapseButton_Container"
          style={{ marginTop: "-2px", marginBottom: "4px" }}
        >
          <ButtonItem
            layout="below"
            bottomSeparator={collapsed ? "standard" : "none"}
            onClick={toggleCollapsed}
          >
            {collapsed ? <RiArrowDownSFill /> : <RiArrowUpSFill />}
          </ButtonItem>
        </div>
      </PanelSectionRow>
      {!collapsed && (
        <PanelSectionRow>
          {file.exists && file.content !== null && file.content !== undefined ? (
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {file.content}
            </pre>
          ) : (
            <Field
              label="File unavailable"
              description={file.error || "The file has not been created yet."}
            />
          )}
        </PanelSectionRow>
      )}
    </>
  );
}

function copyToClipboard(text: string): boolean {
  // document.execCommand is the only channel a Decky frontend can rely on.
  try {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "true");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    document.body.removeChild(textarea);
    return copied;
  } catch {
    return false;
  }
}

async function copyDebugReport() {
  try {
    const response = await writeDebugReport();
    if (!response.report) {
      showErrorToast("Copy failed", response.error || "The debug report is empty.");
      return;
    }
    // The report is generated server side, so copying and writing share it.
    if (copyToClipboard(response.report)) {
      showSuccessToast("Debug report copied", "Paste it anywhere with Ctrl+V.");
    } else {
      showErrorToast("Copy failed", "The file was still written; see the report path.");
    }
    if (response.path) {
      console.log("lsfg-vk debug report:", response.path);
    }
  } catch (error) {
    showErrorToast("Copy failed", error instanceof Error ? error.message : String(error));
  }
}

async function exportDebugReport() {
  try {
    const response = await writeDebugReport();
    if (!response.success) {
      showErrorToast("Export failed", response.error || "Could not write the report.");
      return;
    }
    showSuccessToast(
      "Debug report exported",
      `${response.location === "Downloads" ? "Downloads" : "Home"}: ${response.path}`,
    );
  } catch (error) {
    showErrorToast("Export failed", error instanceof Error ? error.message : String(error));
  }
}

export function ConfigFileTab() {
  const [result, setResult] = useState<DebugFileContentsResult | null>(null);

  useEffect(() => {
    getDebugFileContents().then(setResult).catch((error) => {
      setResult({ success: false, error: String(error) });
    });
  }, []);

  if (!result) {
    return (
      <PanelSection title={t("NERD_CONFIG_FILE", "Config / Debug")}>
        <PanelSectionRow>
          <Spinner />
        </PanelSectionRow>
      </PanelSection>
    );
  }

  return (
    <>
      <style>
        {`
          .LSFG_DebugFileCollapseButton_Container > div > div > div > button,
          .LSFG_DebugFileCollapseButton_Container > div > div > div > div > button {
            height: 24px !important;
            min-height: 24px !important;
            padding: 0 !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
          }

          .LSFG_DebugFileCollapseButton_Container svg {
            display: block;
            margin: 0;
          }
        `}
      </style>
      <PanelSection title={t("NERD_CONFIG_FILE", "Config / Debug")}>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            bottomSeparator="standard"
            onClick={() => {
              void copyDebugReport();
            }}
          >
            Copy debug report
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            bottomSeparator="standard"
            onClick={() => {
              void exportDebugReport();
            }}
          >
            Export debug report
          </ButtonItem>
        </PanelSectionRow>
        {result.error && (
          <PanelSectionRow>
            <Field label="Error" description={result.error} />
          </PanelSectionRow>
        )}
        {result.success && result.files?.map((file) => (
          <DebugFileSection key={file.id} file={file} />
        ))}
      </PanelSection>
    </>
  );
}
