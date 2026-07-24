import { useEffect, useState } from "react";
import { listGameIngestLogs } from "../../api/game-library";
import type { GameIngestLogEvent } from "../../types";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";

const EVENT_LABEL: Record<string, string> = {
  ingest_batch: "立即补全",
  ingest_window: "定时窗口",
  ingest_import: "图库导入",
  discovery_batch: "扩库发现",
};

// 展示顺序 + 中文标签。counts 里同名 key 复用（补全:refreshed/created/not_found/culled/error；
// 扩库:discovered/upserted/new/comments/failed）。chip 只在该 key 存在时渲染，两方向天然各显各的。
const BUCKETS = [
  { key: "refreshed", label: "刷新" },
  { key: "created", label: "新建" },
  { key: "not_found", label: "跳过" },
  { key: "culled", label: "删除" },
  { key: "error", label: "错误" },
  { key: "discovered", label: "发现" },
  { key: "upserted", label: "落库" },
  { key: "new", label: "新增" },
  { key: "comments", label: "精选评论" },
  { key: "failed", label: "失败" },
];

type Dir = "all" | "patrol" | "discovery";
const dirOf = (eventType: string): Dir =>
  eventType === "discovery_batch" ? "discovery" : "patrol";

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN");
}

export function GameIngestLogModal({ onClose }: { onClose: () => void }) {
  const { toast } = useToast();
  const [items, setItems] = useState<GameIngestLogEvent[]>([]);
  const [cursor, setCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [dir, setDir] = useState<Dir>("all");

  function load(next: number | null) {
    setLoading(true);
    listGameIngestLogs({ limit: 30, cursor: next ?? undefined })
      .then((res) => {
        setItems((prev) => (next ? [...prev, ...res.items] : res.items));
        setCursor(res.next_cursor);
        setLoaded(true);
      })
      .catch((e) => toast(e instanceof Error ? e.message : "加载运行日志失败", "error"))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function toggle(id: number) {
    setExpanded((prev) => {
      const n = new Set(prev);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  }

  return (
    <Modal
      title="抓取运行日志"
      onClose={onClose}
      width={640}
      maxHeight={680}
      footer={
        <button className="secondaryButton" type="button" onClick={onClose}>
          关闭
        </button>
      }
    >
      <div className="glLogBody">
        <div className="glGroupSwitch">
          {(
            [
              { key: "all", label: "全部" },
              { key: "discovery", label: "扩库" },
              { key: "patrol", label: "补全" },
            ] as { key: Dir; label: string }[]
          ).map((it) => (
            <button
              key={it.key}
              type="button"
              className={`glSegBtn${dir === it.key ? " active" : ""}`}
              onClick={() => setDir(it.key)}
            >
              {it.label}
            </button>
          ))}
        </div>

        {loaded && items.length === 0 && (
          <div className="glLogEmpty">
            暂无运行记录 —— 跑一次「立即补全一批」/「立即扩库一批」或「从图片库导入」后这里就会有。
          </div>
        )}

        {items
          .filter((ev) => dir === "all" || dirOf(ev.event_type) === dir)
          .map((ev) => {
            const counts = ev.payload_json?.counts ?? {};
            const games = ev.payload_json?.games ?? {};
            const isOpen = expanded.has(ev.id);
            const detailBuckets = BUCKETS.filter((b) => (games[b.key]?.length ?? 0) > 0);
            return (
              <div key={ev.id} className="glLogCard">
                <div className="glLogCardHead">
                  <span className="glLogBadge">{EVENT_LABEL[ev.event_type] ?? ev.event_type}</span>
                  <span className="glLogTime mono">{fmtTime(ev.created_at)}</span>
                </div>
                <div className="glLogMsg">{ev.message}</div>
                <div className="glLogChips">
                  {BUCKETS.filter((b) => typeof counts[b.key] === "number").map((b) => (
                    <span key={b.key} className={`glLogChip glLogChip-${b.key}`}>
                      {b.label} {counts[b.key] as number}
                    </span>
                  ))}
                  {typeof counts.attempts === "number" && (
                    <span className="glLogChip glLogChip-attempts mono">
                      尝试 {counts.attempts}
                    </span>
                  )}
                  {counts.cap_reached === true && (
                    <span className="glLogChip glLogChip-cap">已达尝试上限</span>
                  )}
                </div>
                {detailBuckets.length > 0 && (
                  <button type="button" className="glLogToggle" onClick={() => toggle(ev.id)}>
                    {isOpen ? "收起明细 ▲" : "查看明细 ▼"}
                  </button>
                )}
                {isOpen &&
                  detailBuckets.map((b) => (
                    <div key={b.key} className="glLogDetailRow">
                      <span className={`glLogDetailLabel glLogChip-${b.key}`}>{b.label}</span>
                      <span className="glLogDetailNames">
                        {(games[b.key] ?? []).map((g) => g.name || `#${g.id ?? "?"}`).join("、")}
                      </span>
                    </div>
                  ))}
              </div>
            );
          })}

        {cursor != null && (
          <button
            type="button"
            className="glLogMore"
            disabled={loading}
            onClick={() => load(cursor)}
          >
            {loading ? "加载中…" : "加载更多"}
          </button>
        )}
      </div>
    </Modal>
  );
}
