import { useEffect, useRef, useState } from "react";
import { CalendarClock, Search, SquarePen, Trash2 } from "lucide-react";
import { getIngestConfig, patchIngestConfig } from "../../api/game-library";
import type { GameIngestConfig } from "../../types";
import { useToast } from "../../components/Toast";
import type { SortKey } from "./GameLibraryWorkspace";

type Props = {
  total: number;
  q: string;
  onQ: (v: string) => void;
  sort: SortKey;
  onSort: (s: SortKey) => void;
};

function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function GameLibraryHeader({ total, q, onQ, sort, onSort }: Props) {
  const { toast } = useToast();
  const [flyoutOpen, setFlyoutOpen] = useState(false);
  const [ingest, setIngest] = useState<GameIngestConfig | null>(null);
  const [ingestLoading, setIngestLoading] = useState(false);
  const [toggling, setToggling] = useState(false);
  const flyoutWrapRef = useRef<HTMLDivElement>(null);

  // 首次展开浮层时拉一次配置；已有数据不重复拉（配置弹窗保存后会直接更新本地状态）。
  useEffect(() => {
    if (!flyoutOpen || ingest || ingestLoading) return;
    setIngestLoading(true);
    getIngestConfig()
      .then((cfg) => setIngest(cfg))
      .catch((e) => toast(e instanceof Error ? e.message : "加载抓取配置失败", "error"))
      .finally(() => setIngestLoading(false));
  }, [flyoutOpen, ingest, ingestLoading, toast]);

  // 点击浮层外部收起。
  useEffect(() => {
    if (!flyoutOpen) return;
    function onDocPointerDown(e: MouseEvent) {
      if (flyoutWrapRef.current && !flyoutWrapRef.current.contains(e.target as Node)) {
        setFlyoutOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocPointerDown);
    return () => document.removeEventListener("mousedown", onDocPointerDown);
  }, [flyoutOpen]);

  async function onToggleEnabled() {
    if (!ingest || toggling) return;
    setToggling(true);
    try {
      const next = await patchIngestConfig({ enabled: !ingest.enabled });
      setIngest(next);
    } catch (e) {
      toast(e instanceof Error ? e.message : "更新抓取开关失败", "error");
    } finally {
      setToggling(false);
    }
  }

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

        <button
          type="button"
          className="glHeaderBtn glHeaderBtnEdit"
          onClick={() => {
            /* TODO(H2): 打开编辑信息弹窗（GameEditModal） */
          }}
        >
          <SquarePen size={16} />
          编辑信息
        </button>

        <div className="glIngestFlyoutWrap" ref={flyoutWrapRef}>
          <button
            type="button"
            className="glHeaderBtn glHeaderBtnIngest"
            aria-expanded={flyoutOpen}
            onClick={() => setFlyoutOpen((v) => !v)}
          >
            <CalendarClock size={16} />
            陪衬抓取
          </button>
          {flyoutOpen && (
            <div className="glIngestFlyout">
              <div className="glIngestFlyoutLeft">
                <CalendarClock size={17} color="#A99BFF" aria-hidden />
                <div className="glIngestFlyoutTextGroup">
                  <span className="glIngestFlyoutTitle">陪衬游戏定时抓取</span>
                  <span className="glIngestFlyoutSub">仅用于陪衬库，主推游戏手动维护</span>
                </div>
              </div>
              <div className="glIngestFlyoutRight">
                {ingestLoading || !ingest ? (
                  <span className="glIngestFlyoutSummary">加载中…</span>
                ) : (
                  <>
                    <button
                      type="button"
                      className={`glIngestToggle${ingest.enabled ? " glIngestToggleOn" : ""}`}
                      role="switch"
                      aria-checked={ingest.enabled}
                      aria-label="启用陪衬游戏定时抓取"
                      disabled={toggling}
                      onClick={() => void onToggleEnabled()}
                    >
                      <span className="glIngestToggleKnob" />
                    </button>
                    <span className="glIngestFlyoutTime mono">
                      每天 {ingest.window_start}–{ingest.window_end}
                    </span>
                    <span className="glIngestFlyoutSummary">
                      {ingest.running
                        ? "抓取中…"
                        : ingest.last_run_finished_at
                          ? `上次 ${formatTimestamp(ingest.last_run_finished_at)}`
                          : "尚未运行"}
                    </span>
                    <button
                      type="button"
                      className="glIngestConfigBtn"
                      onClick={() => {
                        /* TODO(G3): 打开抓取设置弹窗（GameIngestSettingsModal） */
                      }}
                    >
                      配置
                    </button>
                  </>
                )}
              </div>
            </div>
          )}
        </div>

        <button
          type="button"
          className="glHeaderBtnDelete glHeaderBtn"
          onClick={() => {
            /* TODO(H2): 确认后删除当前选中游戏 */
          }}
        >
          <Trash2 size={16} color="#FF9CAA" aria-hidden />
          删除游戏
        </button>
      </div>
    </header>
  );
}
