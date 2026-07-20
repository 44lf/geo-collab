import { useEffect, useMemo, useRef, useState } from "react";
import { listCategories } from "../../api/image-library";
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

export function GameLibraryWorkspace() {
  const { toast } = useToast();
  const [raw, setRaw] = useState<GameListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [mainCatIds, setMainCatIds] = useState<Set<number>>(new Set());
  const [q, setQ] = useState("");
  const [seg, setSeg] = useState<Seg>("main");
  const [sort, setSort] = useState<SortKey>("score");
  const [loaded, setLoaded] = useState(false);
  const [catsLoaded, setCatsLoaded] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<GameDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const seqRef = useRef(0);
  const detailSeq = useRef(0);

  // 主推栏目 id 集合（用于把 game 分到 主推/陪衬）。
  useEffect(() => {
    listCategories("main")
      .then((cats) => setMainCatIds(new Set(cats.map((c) => c.id))))
      .catch(() => toast("加载分组信息失败，暂按陪衬展示", "error"))
      .finally(() => setCatsLoaded(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 游戏列表：搜索变化时（服务端 name like）重拉，带竞态防护。
  useEffect(() => {
    const seq = ++seqRef.current;
    const timer = setTimeout(
      () => {
        listGames({ q: q.trim() || undefined, limit: 200 })
          .then((res) => {
            if (seq !== seqRef.current) return;
            setRaw(res.items);
            setTotal(res.total);
            setLoaded(true);
          })
          .catch((e) => {
            if (seq !== seqRef.current) return;
            toast(e instanceof Error ? e.message : "加载游戏失败", "error");
          });
      },
      q ? 250 : 0,
    );
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q]);

  // 主推 = 有桶且桶在 main 集；陪衬 = 其余（含无桶），保证没有游戏消失。
  const isMain = (g: GameListItem) =>
    g.stock_category_id != null && mainCatIds.has(g.stock_category_id);
  const mainCount = useMemo(() => raw.filter(isMain).length, [raw, mainCatIds]);
  const companionCount = raw.length - mainCount;

  const games = useMemo(() => {
    let list = raw.filter((g) => (seg === "main" ? isMain(g) : !isMain(g)));
    list = [...list];
    if (sort === "score") list.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
    else if (sort === "least_used") list.sort((a, b) => a.use_count - b.use_count);
    else if (sort === "recent")
      list.sort((a, b) => (b.last_used_at ?? "").localeCompare(a.last_used_at ?? ""));
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [raw, seg, sort, mainCatIds]);

  const emptyHint = q.trim()
    ? `没有匹配「${q.trim()}」的游戏，换个搜索词`
    : raw.length === 0
      ? "库为空，先在服务器跑 ingest_games 入库"
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
      <GameLibraryHeader total={total} q={q} onQ={setQ} sort={sort} onSort={setSort} />
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
            loaded={loaded && catsLoaded}
            emptyHint={emptyHint}
          />
        </aside>
        <section className="glRight">
          {detailLoading && !detail && <div className="glDetailLoading">加载中…</div>}
          {loaded && catsLoaded && games.length === 0 && (
            <div className="glDetailLoading">{emptyHint}</div>
          )}
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
