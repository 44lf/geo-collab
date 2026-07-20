import { Search } from "lucide-react";
import type { SortKey } from "./GameLibraryWorkspace";

type Props = {
  total: number;
  q: string;
  onQ: (v: string) => void;
  sort: SortKey;
  onSort: (s: SortKey) => void;
};

export function GameLibraryHeader({ total, q, onQ, sort, onSort }: Props) {
  return (
    <header className="glHeader">
      <div className="glTitleZone">
        <p className="glCrumb">素材 / 游戏语料</p>
        <div className="glTitleRow">
          <h1 className="glTitle">游戏库</h1>
          <span className="glCount">
            <b className="mono">{total.toLocaleString("zh-CN")}</b> 款游戏
          </span>
        </div>
      </div>
      <div className="glActions">
        <label className="glSearch">
          <Search size={15} aria-hidden />
          <input
            type="text"
            value={q}
            onChange={(e) => onQ(e.target.value)}
            placeholder="搜索游戏名…"
            aria-label="搜索游戏名"
          />
        </label>
        <select
          className="glSortSelect"
          value={sort}
          onChange={(e) => onSort(e.target.value as SortKey)}
          aria-label="排序"
        >
          <option value="score">评分优先</option>
          <option value="least_used">取材最少</option>
          <option value="recent">最近取材</option>
        </select>
        {/* TODO(game-web-write): 手动入库 / 同步任务 / 删除游戏 按钮 —— 待后端 game_library web 写接口 */}
      </div>
    </header>
  );
}
