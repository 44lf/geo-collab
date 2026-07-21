import React from "react";
import type { ArticleSummary, ReviewStatus } from "../types";

export function formatArticleTemplateSource(article: Pick<ArticleSummary, "source_template_id" | "source_template_name">): string {
  const name = article.source_template_name;
  if (article.source_template_id != null) {
    return `ID：${article.source_template_id}  模板：${name || "—"}`;
  }
  return `模板：${name || "—"}`;
}

export function ReviewBadge({ status }: { status: ReviewStatus }) {
  const approved = status === "approved";
  return (
    <span className={`badge ${approved ? "succeeded" : "waiting_manual_publish"}`}>
      {approved ? "已过审" : "待审核"}
    </span>
  );
}

/**
 * 渲染自评分。后端下发字符串：
 *  - 含 " _ "（如 "65 _ 80"）= 没过线 → 显示「真实分 / 合格线」，整块红色（不管真实分多少）。
 *  - 纯数字（如 "84"）= 过线/老数据 → 按 ≥70绿 / ≥40黄 / <40红 分档上色。
 */
export function renderAutoReviewScore(score: string) {
  const failSep = score.indexOf(" _ ");
  if (failSep >= 0) {
    const real = score.slice(0, failSep);
    const passLine = score.slice(failSep + 3);
    return (
      <span className="badge" style={{ color: "var(--red, #f85149)" }} title="未达本次合格线">
        {real} / {passLine}
      </span>
    );
  }
  const n = Number(score);
  const color =
    n >= 70 ? "var(--green, #3fb950)" : n >= 40 ? "var(--amber, #d29922)" : "var(--red, #f85149)";
  return (
    <span className="badge" style={{ color }}>
      {score}
    </span>
  );
}

export const ArticleListItem = React.memo(function ArticleListItem({
  article,
  draftId,
  selectedIds,
  onToggle,
  onSelect,
}: {
  article: ArticleSummary;
  draftId: number | null;
  selectedIds: number[];
  onToggle: (id: number) => void;
  onSelect: (article: ArticleSummary) => void;
}) {
  return (
    <article className={`articleItem ${article.id === draftId ? "selected" : ""}`}>
      <label className="checkLine">
        <input checked={selectedIds.includes(article.id)} type="checkbox" onChange={() => onToggle(article.id)} />
      </label>
      <button type="button" onClick={() => onSelect(article)}>
        <span style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
          <strong>{article.title}</strong>
          <span
            className="badge"
            style={{ flexShrink: 0, fontFamily: "var(--mono, monospace)", color: "var(--text-muted, #888)" }}
            title="数据库 ID"
          >
            ID {article.id}
          </span>
        </span>
        <span
          className="articleSourceLine"
          style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}
        >
          {article.auto_review_score != null ? (
            <span
              style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
              title="MCP 生文自评分（0-100，取 auto_review_decisions 最新一条）"
            >
              评分：
              {renderAutoReviewScore(article.auto_review_score)}
            </span>
          ) : null}
        </span>
        <span className="articleSourceLine">智能体：{article.source_agent_name || "—"}</span>
        {article.content_type === "xhs_image_text" ? (
          <span className="articleSourceLine">
            <span className="badge">小红书图文</span>
          </span>
        ) : null}
        <span className="articleSourceRow">
          <span className="articleSourceLine">{formatArticleTemplateSource(article)}</span>
          <small>
            {new Date(article.updated_at).toLocaleString()}
            {article.published_count > 0 ? <span style={{ color: "var(--green)", marginLeft: 6 }}>· 已发布 {article.published_count} 次</span> : null}
          </small>
        </span>
      </button>
      <div className="articleItemBadge">
        <ReviewBadge status={article.review_status} />
      </div>
    </article>
  );
});
