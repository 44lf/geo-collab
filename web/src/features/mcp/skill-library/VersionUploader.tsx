import { useState } from "react";
import { Folder, FolderOpen, Loader2, UploadCloud, X } from "lucide-react";
import { uploadSkillVersion } from "../../../api/skills";
import { useToast } from "../../../components/Toast";
import { filesFromDataTransferItems } from "./fileDrop";

const hiddenInput = {
  position: "absolute",
  width: 1,
  height: 1,
  opacity: 0,
  pointerEvents: "none",
} as const;

export function VersionUploader({
  skillId,
  onDone,
  onCancel,
}: {
  skillId: number;
  onDone: () => void;
  onCancel: () => void;
}) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  async function submit(files: File[]) {
    if (!files.length || busy) return;
    setBusy(true);
    try {
      const r = await uploadSkillVersion(skillId, files);
      toast(`已追加 ${r.version_label} 并设为当前`, "success");
      onDone();
    } catch (e) {
      toast(e instanceof Error ? e.message : "追加失败", "error");
      setBusy(false);
    }
  }

  async function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const items = e.dataTransfer.items;
    if (items && items.length > 0 && typeof items[0]?.webkitGetAsEntry === "function") {
      try {
        const files = await filesFromDataTransferItems(items);
        if (files.length) {
          void submit(files);
          return;
        }
      } catch {
        // 回落到普通 FileList
      }
    }
    const files = Array.from(e.dataTransfer.files);
    if (files.length) void submit(files);
  }

  function onPick(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length) void submit(files);
  }

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={(e) => {
        e.preventDefault();
        setDragOver(false);
      }}
      onDrop={(e) => void onDrop(e)}
      style={{
        border: `1.5px dashed ${dragOver ? "var(--accent-deep)" : "var(--accent)"}`,
        borderRadius: "var(--r-lg)",
        background: dragOver ? "rgba(109,107,246,0.10)" : "rgba(109,107,246,0.05)",
        padding: "18px 16px",
        display: "grid",
        gap: 10,
        justifyItems: "center",
        textAlign: "center",
      }}
    >
      {busy ? (
        <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--accent-deep)", fontSize: 13 }}>
          <Loader2 size={15} className="spin" /> 正在追加新版本…
        </div>
      ) : (
        <>
          <UploadCloud size={22} color="var(--accent)" />
          <div style={{ fontSize: 13, color: "var(--fg-2)" }}>拖拽完整的 skill 文件夹 / ZIP 到此，或</div>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", justifyContent: "center" }}>
            <label className="primaryButton" style={{ cursor: "pointer", height: 32, padding: "0 12px" }}>
              <FolderOpen size={13} /> 选择文件
              <input
                type="file"
                multiple
                accept=".zip,.md,application/zip,text/markdown"
                onChange={onPick}
                style={hiddenInput}
              />
            </label>
            <label className="primaryButton" style={{ cursor: "pointer", height: 32, padding: "0 12px" }}>
              <Folder size={13} /> 选择文件夹
              <input
                type="file"
                onChange={onPick}
                style={hiddenInput}
                // @ts-expect-error webkitdirectory 是非标准属性，主流浏览器均支持，但 React DOM 类型未收录
                webkitdirectory=""
              />
            </label>
            <button
              type="button"
              onClick={onCancel}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                color: "var(--fg-3)",
                fontSize: 12.5,
                display: "inline-flex",
                alignItems: "center",
                gap: 4,
              }}
            >
              <X size={13} /> 取消
            </button>
          </div>
          <div style={{ fontSize: 11.5, color: "var(--fg-3)", lineHeight: 1.6 }}>
            新版本会<strong>完全替换</strong>——请上传该 skill 的<strong>全部文件</strong>，少传即丢文件
          </div>
        </>
      )}
    </div>
  );
}
