import { useEffect, useRef, useState, type CSSProperties } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  FileX,
  FolderOpen,
  Info,
  Loader2,
  RotateCcw,
  UploadCloud,
  X,
  XCircle,
} from "lucide-react";
import { listSkillVersions, uploadSkill } from "../../../api/skills";

type Phase = "idle" | "receiving" | "validating" | "storing" | "done" | "error";

const CATEGORY_OPTIONS: { value: string; label: string }[] = [
  { value: "generation", label: "生文" },
  { value: "distribute", label: "发文" },
  { value: "video", label: "视频" },
  { value: "general", label: "通用" },
];

type UploadResult = {
  slug: string;
  version_label: string;
  file_count: number;
  total_bytes: number;
  bundle_sha256: string;
};

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function deriveSkillName(files: File[]): string {
  const withPath = files.find((f) => (f as File & { webkitRelativePath?: string }).webkitRelativePath);
  if (withPath) {
    const rel = (withPath as File & { webkitRelativePath?: string }).webkitRelativePath!;
    return rel.split("/")[0];
  }
  const f = files[0];
  return f.name.toLowerCase().endsWith(".zip") ? f.name.replace(/\.zip$/i, "") : f.name;
}

function classifyError(msg: string): { badge: string } {
  if (/SKILL\.md/i.test(msg)) return { badge: "缺少 SKILL.md" };
  const sizeMatch = msg.match(/(\d+(\.\d+)?)\s*MB/i);
  if (sizeMatch || /过大|超过|too large/i.test(msg)) {
    return { badge: sizeMatch ? `文件过大 · ${sizeMatch[0]}` : "文件过大" };
  }
  return { badge: "上传失败" };
}

// ─── 拖拽文件夹：递归读取 DataTransferItem → FileSystemEntry ───

function withRelativePath(file: File, relativePath: string): File {
  try {
    Object.defineProperty(file, "webkitRelativePath", { value: relativePath, configurable: true });
  } catch {
    // 极少数浏览器禁止重定义只读属性——忽略即可，deriveSkillName 会退回用文件名
  }
  return file;
}

function readAllDirectoryEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const all: FileSystemEntry[] = [];
    const readBatch = () => {
      reader.readEntries((batch) => {
        if (batch.length === 0) {
          resolve(all);
          return;
        }
        all.push(...batch);
        readBatch();
      }, reject);
    };
    readBatch();
  });
}

async function walkEntry(entry: FileSystemEntry, prefix: string, out: File[]): Promise<void> {
  if (entry.isFile) {
    const fileEntry = entry as FileSystemFileEntry;
    const file = await new Promise<File>((resolve, reject) => fileEntry.file(resolve, reject));
    out.push(withRelativePath(file, `${prefix}${entry.name}`));
  } else if (entry.isDirectory) {
    const dirEntry = entry as FileSystemDirectoryEntry;
    const reader = dirEntry.createReader();
    const children = await readAllDirectoryEntries(reader);
    for (const child of children) {
      await walkEntry(child, `${prefix}${entry.name}/`, out);
    }
  }
}

async function filesFromDataTransferItems(items: DataTransferItemList): Promise<File[]> {
  const entries: FileSystemEntry[] = [];
  for (let i = 0; i < items.length; i++) {
    const entry = items[i]?.webkitGetAsEntry?.();
    if (entry) entries.push(entry);
  }
  const out: File[] = [];
  for (const entry of entries) {
    await walkEntry(entry, "", out);
  }
  return out;
}

export function UploadZone({ onUploaded }: { onUploaded: () => void }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [fileName, setFileName] = useState("");
  const [errMsg, setErrMsg] = useState("");
  const [result, setResult] = useState<UploadResult | null>(null);
  const [percent, setPercent] = useState(0);
  const [dragOver, setDragOver] = useState(false);
  const [category, setCategory] = useState<string>("general");

  const inputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);
  const progressTimer = useRef<number | null>(null);
  const dismissTimer = useRef<number | null>(null);
  const phaseTimers = useRef<number[]>([]);

  useEffect(() => {
    const timers = phaseTimers.current;
    return () => {
      if (progressTimer.current) window.clearInterval(progressTimer.current);
      if (dismissTimer.current) window.clearTimeout(dismissTimer.current);
      timers.forEach((t) => window.clearTimeout(t));
    };
  }, []);

  function startFakeProgress() {
    setPercent(4);
    progressTimer.current = window.setInterval(() => {
      setPercent((p) => (p >= 92 ? p : p + Math.max(1, Math.round((92 - p) * 0.12))));
    }, 220);
  }

  function stopFakeProgress(final: number) {
    if (progressTimer.current) {
      window.clearInterval(progressTimer.current);
      progressTimer.current = null;
    }
    setPercent(final);
  }

  function resetToIdle() {
    setPhase("idle");
    setErrMsg("");
    setFileName("");
  }

  async function handleFiles(files: File[]) {
    if (!files.length) return;
    const name = deriveSkillName(files);
    setFileName(files.length === 1 ? files[0].name : `${name}（${files.length} 个文件）`);
    setErrMsg("");
    setResult(null);
    setPhase("receiving");
    startFakeProgress();

    // uploadSkill 是单次请求、没有真实的分阶段回调；这里用短延时驱动三步 tracker 的视觉过渡
    phaseTimers.current.push(
      window.setTimeout(() => setPhase((p) => (p === "receiving" ? "validating" : p)), 260),
      window.setTimeout(() => setPhase((p) => (p === "validating" ? "storing" : p)), 620),
    );

    try {
      const r = await uploadSkill(name, files, category);
      let detail: UploadResult = {
        slug: r.slug,
        version_label: r.version_label,
        file_count: files.length,
        total_bytes: files.reduce((s, f) => s + f.size, 0),
        bundle_sha256: "",
      };
      try {
        const versions = await listSkillVersions(r.skill_id);
        const match =
          versions.versions.find((v) => v.version_label === r.version_label) ??
          versions.versions.find((v) => v.is_current);
        if (match) {
          detail = {
            slug: r.slug,
            version_label: r.version_label,
            file_count: match.file_count,
            total_bytes: match.total_bytes,
            bundle_sha256: match.bundle_sha256,
          };
        }
      } catch {
        // best-effort：取不到版本详情就用本地估算的文件数/字节数，sha 留空不展示
      }
      stopFakeProgress(100);
      setResult(detail);
      setPhase("done");
      onUploaded();
      dismissTimer.current = window.setTimeout(() => {
        setPhase("idle");
        setResult(null);
      }, 5000);
    } catch (e) {
      stopFakeProgress(0);
      setErrMsg(e instanceof Error ? e.message : "上传失败");
      setPhase("error");
    }
  }

  function onDragOver(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(true);
  }
  function onDragLeave(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
  }
  async function onDrop(e: React.DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const items = e.dataTransfer.items;
    if (items && items.length > 0 && typeof items[0]?.webkitGetAsEntry === "function") {
      try {
        const files = await filesFromDataTransferItems(items);
        if (files.length) {
          void handleFiles(files);
          return;
        }
      } catch {
        // 回落到普通 FileList
      }
    }
    const files = Array.from(e.dataTransfer.files);
    if (files.length) void handleFiles(files);
  }
  function onFileInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length) void handleFiles(files);
  }
  function onFolderInputChange(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (files.length) void handleFiles(files);
  }

  const statusText =
    phase === "receiving" ? "正在接收文件…" : phase === "validating" ? "校验 SKILL.md 中…" : "校验通过 · 正在入库…";

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {phase === "error" ? (
        <ErrorCard fileName={fileName} errMsg={errMsg} onRetry={resetToIdle} onDismiss={resetToIdle} />
      ) : phase === "receiving" || phase === "validating" || phase === "storing" ? (
        <UploadingCard fileName={fileName} phase={phase} percent={percent} statusText={statusText} />
      ) : (
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ fontSize: 12.5, color: "var(--fg-2)" }}>业务类别</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              style={{
                height: 30,
                padding: "0 8px",
                borderRadius: "var(--r-sm)",
                border: "1px solid var(--hair)",
                background: "var(--surface-2)",
                color: "var(--fg)",
                fontSize: 12.5,
              }}
            >
              {CATEGORY_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={(e) => void onDrop(e)}
            style={{
              border: `1.5px dashed ${dragOver ? "var(--accent-deep)" : "var(--accent)"}`,
              borderRadius: "var(--r-lg)",
              background: dragOver ? "rgba(109,107,246,0.10)" : "rgba(109,107,246,0.05)",
              padding: "40px 24px",
              textAlign: "center",
              transition: "background .15s, border-color .15s",
            }}
          >
            <div
              style={{
                width: 52,
                height: 52,
                borderRadius: "50%",
                background: "var(--accent-soft)",
                display: "grid",
                placeItems: "center",
                margin: "0 auto 14px",
              }}
            >
              <UploadCloud size={24} color="var(--accent)" />
            </div>
            <div style={{ fontSize: 15, fontWeight: 600, color: "var(--fg)", marginBottom: 10 }}>
              拖拽 ZIP 或文件夹到此上传
            </div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, flexWrap: "wrap" }}>
              <span style={{ fontSize: 13, color: "var(--fg-2)" }}>或</span>
              <label className="primaryButton" style={{ cursor: "pointer", height: 34, padding: "0 14px" }}>
                <FolderOpen size={14} />
                选择文件
                <input
                  ref={inputRef}
                  type="file"
                  multiple
                  onChange={onFileInputChange}
                  accept=".zip,.md,application/zip,text/markdown"
                  style={hiddenInputStyle}
                />
              </label>
              <label style={{ cursor: "pointer", color: "var(--accent-deep)", fontSize: 12.5, textDecoration: "underline" }}>
                选择文件夹
                <input
                  ref={folderInputRef}
                  type="file"
                  onChange={onFolderInputChange}
                  style={hiddenInputStyle}
                  // @ts-expect-error webkitdirectory 是非标准属性，主流浏览器均支持，但 React 的 DOM 类型定义未收录
                  webkitdirectory=""
                />
              </label>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 8, fontSize: 12, color: "var(--fg-3)" }}>
            <Info size={12} />
            单个 skill ≤ 5 MB · 需包含至少一个 SKILL.md · 同名 skill 将追加为新版本，不覆盖旧版本
          </div>
        </div>
      )}

      {phase === "done" && result ? (
        <SuccessToast
          result={result}
          onClose={() => {
            if (dismissTimer.current) window.clearTimeout(dismissTimer.current);
            setPhase("idle");
            setResult(null);
          }}
        />
      ) : null}
    </div>
  );
}

const hiddenInputStyle: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  opacity: 0,
  pointerEvents: "none",
};

function TrackerStep({ label, state }: { label: string; state: "done" | "active" | "pending" }) {
  const color = state === "done" ? "var(--green)" : state === "active" ? "var(--accent-deep)" : "var(--fg-3)";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color }}>
      {state === "done" ? (
        <CheckCircle2 size={12} />
      ) : state === "active" ? (
        <Loader2 size={12} className="spin" />
      ) : (
        <span style={{ width: 12, height: 12, borderRadius: "50%", border: "1px solid var(--fg-3)" }} />
      )}
      {label}
    </span>
  );
}

function UploadingCard({
  fileName,
  phase,
  percent,
  statusText,
}: {
  fileName: string;
  phase: Phase;
  percent: number;
  statusText: string;
}) {
  return (
    <div>
      <div
        style={{
          border: "1px solid rgba(109,107,246,0.35)",
          background: "rgba(109,107,246,0.07)",
          borderRadius: "var(--r-lg)",
          padding: "16px 18px",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <div
            style={{
              width: 36,
              height: 36,
              borderRadius: "50%",
              background: "var(--accent-soft)",
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
            }}
          >
            <Loader2 size={18} className="spin" color="var(--accent)" />
          </div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div
              style={{
                fontWeight: 600,
                color: "var(--fg)",
                fontSize: 14,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {fileName}
            </div>
            <div style={{ fontSize: 12.5, color: "var(--fg-2)", marginTop: 2 }}>{statusText}</div>
          </div>
          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--accent-deep)" }}>{percent}%</div>
        </div>
        <div style={{ height: 6, borderRadius: 99, background: "var(--hair)", marginTop: 12, overflow: "hidden" }}>
          <div
            style={{
              height: "100%",
              width: `${percent}%`,
              background: "var(--grad)",
              transition: "width .2s ease",
            }}
          />
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 10, fontSize: 12.5, flexWrap: "wrap" }}>
        <TrackerStep label="接收文件" state={phase === "receiving" ? "active" : "done"} />
        <ChevronRight size={12} color="var(--fg-3)" />
        <TrackerStep label="校验 SKILL.md" state={phase === "validating" ? "active" : phase === "storing" ? "done" : "pending"} />
        <ChevronRight size={12} color="var(--fg-3)" />
        <TrackerStep label="生成版本 / 入库" state={phase === "storing" ? "active" : "pending"} />
      </div>
    </div>
  );
}

function ErrorCard({
  fileName,
  errMsg,
  onRetry,
  onDismiss,
}: {
  fileName: string;
  errMsg: string;
  onRetry: () => void;
  onDismiss: () => void;
}) {
  const cls = classifyError(errMsg);
  const Icon = cls.badge.startsWith("缺少") ? FileX : cls.badge.startsWith("文件过大") ? AlertTriangle : XCircle;
  return (
    <div
      style={{
        border: "1px solid rgba(248,113,113,0.35)",
        background: "rgba(248,113,113,0.08)",
        borderRadius: "var(--r-lg)",
        padding: "14px 16px",
      }}
    >
      <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
        <div
          style={{
            width: 30,
            height: 30,
            borderRadius: 8,
            background: "var(--red-soft)",
            display: "grid",
            placeItems: "center",
            flexShrink: 0,
            color: "var(--red)",
          }}
        >
          <Icon size={16} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span style={{ fontWeight: 600, color: "var(--fg)", fontSize: 14 }}>{fileName || "上传失败"}</span>
            <span className="badge failed">{cls.badge}</span>
          </div>
          <div style={{ color: "var(--fg-2)", fontSize: 13, marginTop: 6, lineHeight: 1.6 }}>{errMsg}</div>
          <button
            type="button"
            onClick={onRetry}
            style={{
              marginTop: 10,
              display: "inline-flex",
              alignItems: "center",
              gap: 6,
              color: "var(--accent-deep)",
              fontSize: 12.5,
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: 0,
            }}
          >
            <RotateCcw size={12} /> 重新选择文件
          </button>
        </div>
        <button
          type="button"
          onClick={onDismiss}
          aria-label="关闭"
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--fg-3)" }}
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

function SuccessToast({ result, onClose }: { result: UploadResult; onClose: () => void }) {
  return (
    <div
      style={{
        position: "fixed",
        top: 16,
        right: 16,
        zIndex: 2000,
        width: 340,
        background: "var(--surface-2)",
        border: "1px solid rgba(52,211,153,0.30)",
        borderRadius: "var(--r-lg)",
        padding: 16,
        boxShadow: "0 16px 40px rgba(0,0,0,0.5)",
        animation: "slideInRight .3s ease",
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              width: 22,
              height: 22,
              borderRadius: "50%",
              background: "var(--green-soft)",
              display: "grid",
              placeItems: "center",
              flexShrink: 0,
            }}
          >
            <CheckCircle2 size={13} color="var(--green)" />
          </div>
          <span style={{ fontWeight: 650, color: "var(--fg)", fontSize: 14.5 }}>上传成功</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭"
          style={{ background: "none", border: "none", cursor: "pointer", color: "var(--fg-3)" }}
        >
          <X size={14} />
        </button>
      </div>
      <div style={{ color: "var(--fg-2)", fontSize: 13, lineHeight: 1.6, marginTop: 8 }}>
        {result.slug} 已入库，生成新版本 {result.version_label} 并设为当前。旧版本已保留，可随时回滚。
      </div>
      <div style={{ display: "flex", gap: 8, marginTop: 10, flexWrap: "wrap" }}>
        <span className="badge">{result.file_count} 文件 · {formatBytes(result.total_bytes)}</span>
        {result.bundle_sha256 ? <span className="badge">sha {result.bundle_sha256.slice(0, 10)}…</span> : null}
      </div>
      <a
        href={`#skill-card-${result.slug}`}
        onClick={onClose}
        style={{
          marginTop: 10,
          display: "inline-block",
          background: "none",
          border: "none",
          padding: 0,
          cursor: "pointer",
          color: "var(--accent-deep)",
          fontSize: 13,
          fontWeight: 600,
          textDecoration: "none",
        }}
      >
        查看版本历史 →
      </a>
    </div>
  );
}
