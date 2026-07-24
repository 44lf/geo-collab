import { useCallback, useEffect, useRef, useState } from "react";
import { getGame, listGames } from "../../api/game-library";
import type { GameDetail, GameListItem } from "../../types";
import { useToast } from "../../components/Toast";
import { GameLibraryHeader } from "./GameLibraryHeader";
import { GameGroupSwitch } from "./GameGroupSwitch";
import { GameList } from "./GameList";
import { GameDetailCard } from "./GameDetailCard";
import { GameMaterialPanel } from "./GameMaterialPanel";
import { GameSourceTable } from "./GameSourceTable";

export type Seg = "main" | "companion";
export type SortKey = "score" | "least_used" | "recent";

const PAGE_SIZE = 100;

export function GameLibraryWorkspace() {
  const { toast } = useToast();
  const [raw, setRaw] = useState<GameListItem[]>([]); // 当前分组的一页（服务端已按 kind 过滤）
  const [mainCount, setMainCount] = useState(0);
  const [companionCount, setCompanionCount] = useState(0);
  const [q, setQ] = useState("");
  const [seg, setSeg] = useState<Seg>("main");
  const [sort, setSort] = useState<SortKey>("score");
  const [source, setSource] = useState(""); // 来源筛选，空串=全部
  const [page, setPage] = useState(0); // 0-based，当前分组内翻页
  const [loaded, setLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<GameDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const seqRef = useRef(0);
  const detailSeq = useRef(0);

  // 拉当前分组的一页(kind+sort+分页都在服务端)展示，另一分组只取总数(limit 1)。计数一律用
  // 服务端 total，保证「主推 + 陪衬 == 顶部总数」；排序落 SQL，翻页跨页顺序才正确。
  const reloadGames = useCallback(() => {
    const seq = ++seqRef.current;
    const query = q.trim() || undefined;
    const src = source || undefined;
    const other: Seg = seg === "main" ? "companion" : "main";
    return Promise.all([
      listGames({
        q: query,
        kind: seg,
        source: src,
        sort,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
      listGames({ q: query, kind: other, source: src, limit: 1 }),
    ])
      .then(([cur, oth]) => {
        if (seq !== seqRef.current) return;
        setRaw(cur.items);
        if (seg === "main") {
          setMainCount(cur.total);
          setCompanionCount(oth.total);
        } else {
          setCompanionCount(cur.total);
          setMainCount(oth.total);
        }
        setLoaded(true);
      })
      .catch((e) => {
        if (seq !== seqRef.current) return;
        toast(e instanceof Error ? e.message : "加载游戏失败", "error");
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, seg, sort, source, page]);

  // 过滤键（搜索/分组/排序/来源）变化时回到第 1 页，避免停在越界页码上。
  useEffect(() => {
    setPage(0);
  }, [q, seg, sort, source]);

  // 列表重拉：过滤键或页码变化时触发（服务端 name like + kind + source + sort + 分页），竞态防护 + 防抖。
  useEffect(() => {
    const timer = setTimeout(() => void reloadGames(), q ? 250 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, seg, sort, source, page]);

  const total = mainCount + companionCount;
  const segCount = seg === "main" ? mainCount : companionCount;
  const totalPages = Math.max(1, Math.ceil(segCount / PAGE_SIZE));
  const games = raw; // 服务端已按 kind 过滤 + 排序 + 分页，直接用

  const emptyHint = q.trim()
    ? `没有匹配「${q.trim()}」的游戏，换个搜索词`
    : total === 0
      ? "库为空，先在服务器跑 ingest 入库"
      : `「${seg === "main" ? "主推游戏" : "陪衬游戏"}」分组暂无游戏，切到另一分组看看`;

  async function openDetail(id: number) {
    setSelectedId(id);
    const seq = ++detailSeq.current;
    setDetailLoading(true);
    try {
      const d = await getGame(id);
      if (seq === detailSeq.current) setDetail(d);
    } catch (e) {
      toast(e instanceof Error ? e.message : "加载游戏详情失败", "error");
    } finally {
      if (seq === detailSeq.current) setDetailLoading(false);
    }
  }

  // 列表变化后自动选中首项（或当前选中已不在列表时）。
  useEffect(() => {
    if (games.length === 0) {
      setSelectedId(null);
      setDetail(null);
      return;
    }
    if (selectedId == null || !games.some((g) => g.game_id === selectedId)) {
      void openDetail(games[0].game_id);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [games]);

  return (
    <div className="glPage">
      <GameLibraryHeader
        total={total}
        q={q}
        onQ={setQ}
        sort={sort}
        onSort={setSort}
        source={source}
        onSource={setSource}
        newGameKind={seg}
        onGamesChanged={reloadGames}
        selectedGame={detail}
        onGameSaved={(updated) => {
          setDetail(updated);
          void reloadGames();
        }}
        onGameDeleted={() => {
          setSelectedId(null);
          setDetail(null);
          void reloadGames();
        }}
      />
      <div className="glBody">
        <aside className="glLeft">
          <GameGroupSwitch
            seg={seg}
            onSeg={setSeg}
            mainCount={mainCount}
            companionCount={companionCount}
          />
          <GameList
            games={games}
            selectedId={selectedId}
            onSelect={openDetail}
            loaded={loaded}
            emptyHint={emptyHint}
          />
          {totalPages > 1 && (
            <div className="glPager">
              <button
                type="button"
                className="glPagerBtn"
                disabled={page <= 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                ‹ 上一页
              </button>
              <span className="glPagerInfo mono">
                {page + 1} / {totalPages}
              </span>
              <button
                type="button"
                className="glPagerBtn"
                disabled={page >= totalPages - 1}
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
              >
                下一页 ›
              </button>
            </div>
          )}
        </aside>
        <section className="glRight">
          {detailLoading && !detail && <div className="glDetailLoading">加载中…</div>}
          {loaded && games.length === 0 && <div className="glDetailLoading">{emptyHint}</div>}
          {detail && <GameDetailCard detail={detail} />}
          {detail && (
            <div className="glMaterialRow">
              <GameMaterialPanel
                key={detail.game_id}
                categoryId={detail.stock_category_id}
                screenshotUrlCount={detail.screenshot_urls.length}
              />
              <GameSourceTable detail={detail} />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
