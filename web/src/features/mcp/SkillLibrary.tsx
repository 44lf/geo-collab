import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2, PackageOpen } from "lucide-react";
import { listSkills, listSkillVersions, deleteSkill, type Skill } from "../../api/skills";
import { UploadZone } from "./skill-library/UploadZone";
import { SkillCard } from "./skill-library/SkillCard";
import { ConfirmDialog } from "./skill-library/ConfirmDialog";
import { useToast } from "../../components/Toast";

export function SkillLibrary({ isAdmin, currentUserId }: { isAdmin: boolean; currentUserId: number | null }) {
  const { toast } = useToast();
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [error, setError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<{ skill: Skill; versionCount: number | null } | null>(null);
  const [deleting, setDeleting] = useState(false);

  const reload = useCallback(() => {
    listSkills()
      .then((r) => {
        setSkills(r.skills);
        setError("");
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : "加载 Skill 库失败"));
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  const onDeleteSkill = useCallback((skill: Skill) => {
    setDeleteTarget({ skill, versionCount: null });
    listSkillVersions(skill.id)
      .then((r) => {
        setDeleteTarget((cur) => (cur && cur.skill.id === skill.id ? { skill, versionCount: r.versions.length } : cur));
      })
      .catch(() => {
        // best-effort：拿不到版本数就不显示具体数字，不影响删除本身
      });
  }, []);

  const confirmDeleteSkill = useCallback(async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteSkill(deleteTarget.skill.id);
      toast(`已删除 ${deleteTarget.skill.name}`, "success");
      setDeleteTarget(null);
      reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    } finally {
      setDeleting(false);
    }
  }, [deleteTarget, reload, toast]);

  // 官方 skill 置顶，其余保持原有顺序（Array.prototype.sort 稳定排序）
  const sorted = skills ? [...skills].sort((a, b) => Number(b.is_official) - Number(a.is_official)) : null;

  return (
    <section className="panel" style={{ display: "grid", gap: 16 }}>
      <div>
        <h2 style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, fontSize: 17 }}>
          <PackageOpen size={18} /> Skill 库 · 上传与版本管理
        </h2>
        <p style={{ color: "var(--fg-2)", fontSize: 13, lineHeight: 1.7, margin: 0 }}>
          直接在网页上传 Skill（单个 SKILL.md、多文件 ZIP 或文件夹），自动入库并生成新版本号——
          无需改代码、无需重新部署。下方可下载 ZIP，也可以直接让 Claude Code 自动安装。
        </p>
      </div>

      <UploadZone onUploaded={reload} />

      {error ? (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 14px",
            borderRadius: "var(--r)",
            background: "var(--red-soft)",
            color: "var(--red)",
            fontSize: 13,
          }}
        >
          <AlertTriangle size={14} /> {error}
        </div>
      ) : null}

      <div>
        <div style={{ fontSize: 13, color: "var(--fg-2)", marginBottom: 10, fontWeight: 600 }}>
          已入库 SKILL · {sorted ? sorted.length : "…"}
        </div>

        {sorted === null ? (
          <div style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--fg-3)", fontSize: 13 }}>
            <Loader2 size={14} className="spin" /> 加载中…
          </div>
        ) : sorted.length === 0 ? (
          <div
            style={{
              display: "grid",
              justifyItems: "center",
              gap: 8,
              padding: "36px 20px",
              border: "1px dashed var(--hair-2)",
              borderRadius: "var(--r-lg)",
              textAlign: "center",
            }}
          >
            <PackageOpen size={28} style={{ color: "var(--fg-3)" }} />
            <div style={{ fontWeight: 600, color: "var(--fg)" }}>还没有任何 Skill</div>
            <div style={{ color: "var(--fg-2)", fontSize: 13, maxWidth: 360 }}>
              把 SKILL.md、skill 目录或 .zip 拖到上方上传区，即可创建库里第一个 skill 并自动生成 v1。
            </div>
          </div>
        ) : (
          <div style={{ display: "grid", gap: 10 }}>
            {sorted.map((s) => (
              <SkillCard
                key={s.id}
                skill={s}
                isAdmin={isAdmin}
                currentUserId={currentUserId}
                onChanged={reload}
                onDeleteSkill={onDeleteSkill}
              />
            ))}
          </div>
        )}
      </div>

      {deleteTarget ? (
        <ConfirmDialog
          tone="red"
          title={`删除整个 ${deleteTarget.skill.name}？`}
          confirmLabel={deleting ? "删除中…" : "删除整个 skill"}
          onCancel={() => setDeleteTarget(null)}
          onConfirm={() => void confirmDeleteSkill()}
          body={
            <>
              <p style={{ margin: 0 }}>
                将把 {deleteTarget.skill.name} 连同它
                {deleteTarget.versionCount !== null ? `全部 ${deleteTarget.versionCount} 个历史版本` : "全部历史版本"}
                一起移出 Skill 库。此操作不可撤销，已经下载 / 装到本机的副本不受影响。
              </p>
              <div
                style={{
                  padding: "8px 12px",
                  borderRadius: "var(--r-sm)",
                  background: "var(--red-soft)",
                  color: "var(--red)",
                  fontSize: 12.5,
                  fontWeight: 600,
                  border: "1px solid rgba(248,113,113,0.3)",
                }}
              >
                <AlertTriangle size={13} style={{ marginRight: 6, verticalAlign: -2 }} />
                连同
                {deleteTarget.versionCount !== null ? ` ${deleteTarget.versionCount} 个版本` : "全部版本"}
                一并永久删除
              </div>
            </>
          }
        />
      ) : null}
    </section>
  );
}
