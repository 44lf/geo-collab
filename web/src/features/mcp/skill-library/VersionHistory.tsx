import { useCallback, useEffect, useState, type CSSProperties } from "react";
import { AlertTriangle, Check, Copy, RotateCcw, Trash2 } from "lucide-react";
import {
  listSkillVersions,
  setCurrentVersion,
  deleteSkillVersion,
  skillInstallCommand,
  type SkillVersion,
} from "../../../api/skills";
import { ConfirmDialog } from "./ConfirmDialog";
import { useToast } from "../../../components/Toast";

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function uploaderLabel(uploadedBy: number | null): string {
  return uploadedBy === null ? "系统内置" : `用户 #${uploadedBy}`;
}

export function VersionHistory({
  skill,
  canDelete,
  onChanged,
}: {
  skill: { id: number; slug: string; is_official: boolean };
  canDelete: (v: SkillVersion) => boolean;
  onChanged: () => void;
}) {
  const { toast } = useToast();
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<{ kind: "rollback" | "delete"; v: SkillVersion } | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const reload = useCallback(() => {
    setLoading(true);
    listSkillVersions(skill.id)
      .then((r) => {
        setVersions(r.versions);
        setError("");
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "加载版本历史失败"))
      .finally(() => setLoading(false));
  }, [skill.id]);

  useEffect(() => {
    reload();
  }, [reload]);

  const current = versions.find((v) => v.is_current) ?? null;

  const onConfirmAction = useCallback(async () => {
    if (!confirm) return;
    setBusy(true);
    try {
      if (confirm.kind === "rollback") {
        await setCurrentVersion(skill.id, confirm.v.id);
        toast(`已切回 ${confirm.v.version_label}`, "success");
      } else {
        await deleteSkillVersion(skill.id, confirm.v.id);
        toast(`已删除 ${confirm.v.version_label}`, "success");
      }
      setConfirm(null);
      reload();
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : "操作失败", "error");
    } finally {
      setBusy(false);
    }
  }, [confirm, onChanged, reload, skill.id, toast]);

  const installCommand = skillInstallCommand(skill.slug);

  const onCopyInstall = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(installCommand);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast("复制失败，请手动选择文本", "error");
    }
  }, [installCommand, toast]);

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ fontSize: 12.5, color: "var(--fg-2)", fontWeight: 600 }}>
        版本历史 · {skill.slug}
        {skill.is_official ? "" : "（自定义）"}
      </div>

      {loading ? (
        <div style={{ fontSize: 12.5, color: "var(--fg-3)" }}>加载中…</div>
      ) : error ? (
        <div style={{ fontSize: 12.5, color: "var(--red)" }}>{error}</div>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5 }}>
            <thead>
              <tr style={{ textAlign: "left", color: "var(--fg-3)" }}>
                <th style={thStyle}>版本</th>
                <th style={thStyle}>上传时间</th>
                <th style={thStyle}>上传人</th>
                <th style={thStyle}>SHA-256</th>
                <th style={thStyle}>操作</th>
              </tr>
            </thead>
            <tbody>
              {versions.map((v) => {
                const allowDelete = canDelete(v);
                return (
                  <tr key={v.id} style={{ borderTop: "1px solid var(--hair)" }}>
                    <td style={tdStyle}>
                      <span style={{ fontWeight: 600, color: "var(--fg)" }}>{v.version_label}</span>{" "}
                      {v.is_current ? <span style={currentPillStyle}>当前</span> : null}
                    </td>
                    <td style={tdStyle}>{new Date(v.uploaded_at).toLocaleString()}</td>
                    <td style={tdStyle}>{uploaderLabel(v.uploaded_by)}</td>
                    <td style={tdStyle}>
                      <code style={codeStyle}>{v.bundle_sha256.slice(0, 12)}…</code>
                    </td>
                    <td style={{ ...tdStyle }}>
                      <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                        {v.is_current ? (
                          <button
                            type="button"
                            disabled
                            style={{
                              ...pillButtonStyle,
                              background: "var(--green-soft)",
                              color: "var(--green)",
                              cursor: "default",
                            }}
                          >
                            <Check size={12} /> 已是当前
                          </button>
                        ) : (
                          <button
                            type="button"
                            style={{ ...pillButtonStyle, background: "var(--accent-soft)", color: "var(--accent-deep)" }}
                            onClick={() => setConfirm({ kind: "rollback", v })}
                          >
                            <RotateCcw size={12} /> 设为当前
                          </button>
                        )}
                        <button
                          type="button"
                          className="iconButton"
                          style={{ width: 26, height: 26, color: allowDelete ? "var(--red)" : undefined }}
                          disabled={!allowDelete}
                          title={allowDelete ? "删除该版本" : "无权限删除"}
                          onClick={() => setConfirm({ kind: "delete", v })}
                        >
                          <Trash2 size={12} />
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div
        style={{
          marginTop: 6,
          padding: 12,
          borderRadius: "var(--r)",
          background: "var(--bg-2)",
          border: "1px solid var(--hair)",
        }}
      >
        <div style={{ fontSize: 12.5, color: "var(--fg-2)", marginBottom: 6 }}>
          让 Claude Code 自己装（推荐 · 需已配 MCP token）
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <code
            style={{
              flex: 1,
              fontFamily: "var(--mono)",
              fontSize: 12.5,
              background: "var(--surface-2)",
              border: "1px solid var(--hair)",
              borderRadius: "var(--r-sm)",
              padding: "8px 10px",
              overflowX: "auto",
              whiteSpace: "nowrap",
              color: "var(--fg)",
            }}
          >
            {installCommand}
          </code>
          <button type="button" className="iconButton" onClick={() => void onCopyInstall()} title="复制命令">
            {copied ? <Check size={14} /> : <Copy size={14} />}
          </button>
        </div>
      </div>

      {confirm ? (
        <ConfirmDialog
          tone={confirm.kind === "rollback" ? "purple" : "red"}
          title={confirm.kind === "rollback" ? `回滚到 ${confirm.v.version_label}？` : `删除版本 ${confirm.v.version_label}？`}
          confirmLabel={busy ? "处理中…" : confirm.kind === "rollback" ? "确认回滚" : "删除该版本"}
          onCancel={() => setConfirm(null)}
          onConfirm={() => void onConfirmAction()}
          body={
            confirm.kind === "rollback" ? (
              <>
                <p style={{ margin: 0 }}>
                  将把 {skill.slug} 的当前版本从 {current ? current.version_label : "—"} 切回{" "}
                  {confirm.v.version_label}。之后下载 ZIP、让 Claude Code 自动安装、以及 install_loop_skills
                  三个下游都会取到 {confirm.v.version_label}。此操作不删除任何版本，可随时再切回。
                </p>
                <div style={diffRowStyle}>
                  <span style={diffChipStyle}>当前 {current ? current.version_label : "—"}</span>
                  <span style={{ color: "var(--fg-3)" }}>→</span>
                  <span style={{ ...diffChipStyle, borderColor: "rgba(109,107,246,0.45)", color: "var(--accent-deep)" }}>
                    回滚为 {confirm.v.version_label}
                  </span>
                </div>
              </>
            ) : (
              <>
                <p style={{ margin: 0 }}>
                  将从 {skill.slug} 的历史中永久移除 {confirm.v.version_label}（
                  {uploaderLabel(confirm.v.uploaded_by)} · {new Date(confirm.v.uploaded_at).toLocaleDateString()} ·{" "}
                  {formatBytes(confirm.v.total_bytes)}）。此操作不可撤销，当前版本{" "}
                  {current ? current.version_label : "—"} 不受影响。
                </p>
                <div style={warnStripStyle}>
                  <AlertTriangle size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
                  永久删除该版本，不可恢复
                </div>
              </>
            )
          }
        />
      ) : null}
    </div>
  );
}

const thStyle: CSSProperties = { padding: "6px 8px", fontWeight: 500 };
const tdStyle: CSSProperties = { padding: "8px", color: "var(--fg-2)", verticalAlign: "middle" };
const codeStyle: CSSProperties = { fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--fg-2)" };
const currentPillStyle: CSSProperties = {
  fontSize: 10.5,
  fontWeight: 700,
  color: "var(--green)",
  background: "var(--green-soft)",
  padding: "1px 6px",
  borderRadius: 99,
  border: "1px solid rgba(52,211,153,0.35)",
};
const pillButtonStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 4,
  fontSize: 11.5,
  fontWeight: 600,
  padding: "4px 9px",
  borderRadius: 99,
  border: "none",
  cursor: "pointer",
};
const diffRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 10,
  padding: "10px 12px",
  background: "var(--bg-2)",
  borderRadius: "var(--r)",
  border: "1px solid var(--hair)",
};
const diffChipStyle: CSSProperties = {
  padding: "3px 10px",
  borderRadius: 8,
  background: "var(--surface-2)",
  border: "1px solid var(--hair-2)",
  fontFamily: "var(--mono)",
  fontSize: 12.5,
  color: "var(--fg)",
};
const warnStripStyle: CSSProperties = {
  padding: "8px 12px",
  borderRadius: "var(--r-sm)",
  background: "var(--red-soft)",
  color: "var(--red)",
  fontSize: 12.5,
  fontWeight: 600,
  border: "1px solid rgba(248,113,113,0.3)",
};
