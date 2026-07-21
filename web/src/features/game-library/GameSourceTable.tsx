import type { GameDetail } from "../../types";

export function GameSourceTable({ detail: d }: { detail: GameDetail }) {
  const sourceNames = [...new Set(d.sources.map((s) => s.source).filter(Boolean))].join(", ");
  const rows: { k: string; v: string }[] = [
    { k: "source", v: sourceNames || "—" },
    { k: "name_normalized", v: d.name_normalized },
    { k: "game_tags", v: d.tags.join(" / ") || "—" },
    { k: "screenshot_urls", v: `${d.screenshot_urls.length} 条，已去重` },
    { k: "last_used_article_id", v: d.last_used_article_id ? `#${d.last_used_article_id}` : "—" },
  ];
  return (
    <div className="glSourceTable">
      <div className="glSourceHead">入库来源</div>
      {rows.map((r) => (
        <div key={r.k} className="glSourceRow">
          <span className="glSourceKey mono">{r.k}</span>
          <span className="glSourceVal" title={r.v}>{r.v}</span>
        </div>
      ))}
    </div>
  );
}
