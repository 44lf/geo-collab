import { useState, type CSSProperties } from "react";
import { ChevronDown, ChevronUp, Download, FileText, ShieldCheck, Trash2, User } from "lucide-react";
import { skillDownloadUrl, type Skill, type SkillVersion } from "../../../api/skills";
import { VersionHistory } from "./VersionHistory";

function formatBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function formatMonthDay(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function uploaderLabel(uploadedBy: number | null): string {
  return uploadedBy === null ? "系统内置" : `用户 #${uploadedBy}`;
}

function OfficialBadge() {
  return (
    <span style={badgeStyle("var(--accent-soft)", "var(--accent-deep)", "rgba(109,107,246,0.35)")}>
      <ShieldCheck size={11} /> 官方
    </span>
  );
}

function CustomBadge() {
  return (
    <span style={badgeStyle("var(--cream-2)", "var(--fg-2)", "var(--hair)")}>
      <User size={11} /> 自定义
    </span>
  );
}

const CATEGORY_ZH: Record<string, string> = {
  generation: "生文",
  distribute: "发文",
  video: "视频",
  general: "通用",
};

function CategoryBadge({ category }: { category: string }) {
  return (
    <span style={badgeStyle("var(--cream-2)", "var(--fg-2)", "var(--hair)")}>
      {CATEGORY_ZH[category] ?? category}
    </span>
  );
}

function badgeStyle(bg: string, color: string, border: string): CSSProperties {
  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 4,
    padding: "2px 8px",
    borderRadius: 99,
    fontSize: 11,
    fontWeight: 600,
    background: bg,
    color,
    border: `1px solid ${border}`,
  };
}

export function SkillCard({
  skill,
  isAdmin,
  currentUserId,
  onChanged,
  onDeleteSkill,
}: {
  skill: Skill;
  isAdmin: boolean;
  currentUserId: number | null;
  onChanged: () => void;
  onDeleteSkill: (s: Skill) => void;
}) {
  const [expanded, setExpanded] = useState(false);

  const canDeleteVersion = (v: SkillVersion) =>
    isAdmin || (!skill.is_official && v.uploaded_by === currentUserId);
  const canDeleteSkill = isAdmin;

  return (
    <div
      id={`skill-card-${skill.slug}`}
      style={{
        border: "1px solid var(--hair)",
        borderRadius: "var(--r-lg)",
        background: "var(--glass)",
        padding: "14px 16px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <FileText size={16} style={{ color: "var(--fg-3)", flexShrink: 0 }} />
        <span style={{ fontWeight: 650, color: "var(--fg)", fontSize: 14.5 }}>{skill.name}</span>
        {skill.is_official ? <OfficialBadge /> : <CustomBadge />}
        <CategoryBadge category={skill.category} />
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="secondaryButton"
            style={{ height: 30, padding: "0 10px", fontSize: 12.5 }}
            onClick={() => setExpanded((v) => !v)}
          >
            版本历史 {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
          </button>
          <a
            className="secondaryButton"
            style={{ height: 30, padding: "0 10px", fontSize: 12.5, textDecoration: "none" }}
            href={skillDownloadUrl(skill.id)}
            download
          >
            <Download size={13} /> ZIP
          </a>
          <button
            type="button"
            className="iconButton"
            title={canDeleteSkill ? "删除整个 skill" : "仅 admin 可删除"}
            disabled={!canDeleteSkill}
            onClick={() => onDeleteSkill(skill)}
            style={{ color: canDeleteSkill ? "var(--red)" : undefined }}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      <div style={{ marginTop: 6, fontSize: 12.5, color: "var(--fg-2)" }}>
        当前 {skill.current_version_label ?? "—"} · {skill.file_count} 文件 · {formatBytes(skill.total_bytes)} ·
        更新于 {formatMonthDay(skill.updated_at)} · by {uploaderLabel(skill.uploaded_by)}
      </div>

      {expanded ? (
        <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--hair)" }}>
          <VersionHistory skill={skill} canDelete={canDeleteVersion} onChanged={onChanged} />
        </div>
      ) : null}
    </div>
  );
}
