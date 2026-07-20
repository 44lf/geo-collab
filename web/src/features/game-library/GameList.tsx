import { Gamepad2 } from "lucide-react";
import type { GameListItem } from "../../types";

type Props = {
  games: GameListItem[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  loaded: boolean;
};

export function GameList({ games, selectedId, onSelect, loaded }: Props) {
  return (
    <div className="glList">
      {loaded && games.length === 0 && (
        <div className="glListEmpty">
          <Gamepad2 size={30} strokeWidth={1.3} />
          <p>没有匹配的游戏</p>
          <span>换个分组/搜索词；库为空时先跑 ingest_games</span>
        </div>
      )}
      {games.map((g) => (
        <button
          key={g.game_id}
          type="button"
          className={`glRow${selectedId === g.game_id ? " active" : ""}`}
          onClick={() => onSelect(g.game_id)}
        >
          {g.icon_url ? (
            <img className="glRowIcon" src={g.icon_url} alt="" loading="lazy" />
          ) : (
            <div className="glRowIcon glRowIconFallback">{g.name.slice(0, 1)}</div>
          )}
          <div className="glRowMain">
            <div className="glRowTop">
              <span className="glRowName" title={g.name}>{g.name}</span>
              {g.score != null && <span className="glRowScore mono">score {g.score.toFixed(1)}</span>}
            </div>
            <div className="glRowBottom">
              <span className="glRowTags" title={g.tags.join(" · ")}>
                {g.tags.slice(0, 3).join(" · ") || "无标签"}
              </span>
              <span className="glRowUse mono">use {g.use_count}</span>
            </div>
          </div>
        </button>
      ))}
    </div>
  );
}
