import { BookmarkCheck, PenLine } from "lucide-react";
import type { GameDetail } from "../../types";

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("zh-CN");
}
function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("zh-CN");
}
function fmtCount(n: number | null): string {
  if (n == null) return "—";
  if (n >= 10000) return `${(n / 10000).toFixed(n % 10000 === 0 ? 0 : 1)}万+`;
  return n.toLocaleString("zh-CN");
}

export function GameDetailCard({ detail: d }: { detail: GameDetail }) {
  const sourceNames = [...new Set(d.sources.map((s) => s.source).filter(Boolean))].join(" · ");
  const tiles: { label: string; value: string; sub: string }[] = [
    { label: "入库来源", value: sourceNames || "—", sub: "跨源合并" },
    { label: "支持平台", value: d.platforms.join(" / ") || "—", sub: "平台覆盖" },
    { label: "引用次数", value: String(d.use_count), sub: "取材回写" },
    { label: "素材栏目", value: d.stock_category_id ? `#${d.stock_category_id}` : "—", sub: "截图归档" },
    { label: "最近校验", value: fmtDateTime(d.last_verified_at), sub: "新鲜度" },
  ];
  const sourceGameIds =
    d.sources.map((s) => `${s.source ?? "—"}/${s.source_game_id ?? "—"}`).join(" · ") || "—";
  return (
    <div className="glCard">
      <div className="glCardTop">
        <div className="glCardId">
          {d.icon_url ? (
            <img className="glCardIcon" src={d.icon_url} alt="" />
          ) : (
            <div className="glCardIcon glRowIconFallback">{d.name.slice(0, 1)}</div>
          )}
          <div className="glCardIdText">
            <div className="glCardNameRow">
              <h2>{d.name}</h2>
              {!d.is_active && <span className="glBadgeInactive">已下架</span>}
            </div>
            <div className="glCardTags">
              {d.tags.map((t) => (
                <span key={t} className="glChip">{t}</span>
              ))}
            </div>
          </div>
        </div>
        <div className="glCardScore">
          <span className="glBigScore mono">{d.score != null ? d.score.toFixed(1) : "—"}</span>
          <span className="glScoreCap">综合评分 · score</span>
          <div className="glScoreMetrics mono">
            <span>评论 {fmtCount(d.comment_count)}</span>
            <span>平台 {d.platforms.length}</span>
          </div>
        </div>
      </div>

      <div className="glStatusBar">
        <span className="glStatusLeft">
          <PenLine size={15} />
          生文取材详情：主游戏候选 · 可绑定截图素材 · 保存文章后回写 use_count
        </span>
        <span className="glStatusRight mono">selected_games.game_id = {d.game_id}</span>
      </div>

      <div className="glMetricRow">
        {tiles.map((t) => (
          <div key={t.label} className="glMetricTile">
            <span className="glMetricLabel">{t.label}</span>
            <span className="glMetricValue" title={t.value}>{t.value}</span>
            <span className="glMetricSub">{t.sub}</span>
          </div>
        ))}
      </div>

      {d.description && <p className="glDesc">{d.description}</p>}

      <div className="glCardBottom">
        <div className="glCardBottomLeft">
          <span className="glPill"><BookmarkCheck size={14} /> 被引用 {d.use_count} 次</span>
          <span className="mono glMeta">last_used_at：{fmtDateTime(d.last_used_at)}</span>
          <span className="mono glMeta">first_seen_at：{fmtDate(d.first_seen_at)}</span>
        </div>
        <span className="mono glMeta">source_game_id: {sourceGameIds}</span>
      </div>
    </div>
  );
}
