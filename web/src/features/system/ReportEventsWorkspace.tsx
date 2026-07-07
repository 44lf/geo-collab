import { useEffect, useState } from "react";
import { RefreshCw, Search, RotateCcw } from "lucide-react";
import { api } from "../../api/client";
import { useToast } from "../../components/Toast";
import { Modal } from "../../components/Modal";
import { formatDateTime } from "../../utils/dateFormat";

type ReportEventItem = {
  id: number;
  created_at: string;
  source_module: string;
  source_type: string | null;
  source_id: number | null;
  event_type: string;
  level: string;
  message: string;
  payload_json: unknown | null;
};

type ReportEventListResponse = {
  items: ReportEventItem[];
  next_cursor: number | null;
};

type Filters = {
  source_module: string;
  source_type: string;
  source_id: string;
  event_type: string;
  level: string;
  start_at: string;
  end_at: string;
};

const EMPTY_FILTERS: Filters = {
  source_module: "",
  source_type: "",
  source_id: "",
  event_type: "",
  level: "",
  start_at: "",
  end_at: "",
};

function buildQuery(filters: Filters, cursor: number | null, limit = 100): string {
  const params = new URLSearchParams();
  if (filters.source_module.trim()) params.set("source_module", filters.source_module.trim());
  if (filters.source_type.trim()) params.set("source_type", filters.source_type.trim());
  if (filters.source_id.trim()) params.set("source_id", filters.source_id.trim());
  if (filters.event_type.trim()) params.set("event_type", filters.event_type.trim());
  if (filters.level.trim()) params.set("level", filters.level.trim());
  if (filters.start_at) {
    const d = new Date(filters.start_at);
    if (!Number.isNaN(d.getTime())) params.set("start_at", d.toISOString());
  }
  if (filters.end_at) {
    const d = new Date(filters.end_at);
    if (!Number.isNaN(d.getTime())) params.set("end_at", d.toISOString());
  }
  if (cursor !== null) params.set("cursor", String(cursor));
  params.set("limit", String(limit));
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

// 借用现成的语义色 badge class，不新增 CSS。
function levelBadgeClass(level: string): string {
  switch (level) {
    case "error":
      return "failed";
    case "warning":
      return "waiting_manual_publish";
    case "info":
    default:
      return "running";
  }
}

function truncate(value: string, max: number): string {
  if (value.length <= max) return value;
  return `${value.slice(0, max)}…`;
}

export function ReportEventPanel() {
  const { toast } = useToast();
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [appliedFilters, setAppliedFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<ReportEventItem[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [detailItem, setDetailItem] = useState<ReportEventItem | null>(null);

  async function loadFirstPage(currentFilters: Filters) {
    setLoading(true);
    try {
      const data = await api<ReportEventListResponse>(`/api/report-events${buildQuery(currentFilters, null)}`);
      setItems(data.items);
      setNextCursor(data.next_cursor);
    } catch (err) {
      toast(err instanceof Error ? err.message : "加载打点日志失败", "error");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    if (nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    try {
      const data = await api<ReportEventListResponse>(
        `/api/report-events${buildQuery(appliedFilters, nextCursor)}`,
      );
      setItems((prev) => [...prev, ...data.items]);
      setNextCursor(data.next_cursor);
    } catch (err) {
      toast(err instanceof Error ? err.message : "加载下一页失败", "error");
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    void loadFirstPage(EMPTY_FILTERS);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleApplyFilters(e: React.FormEvent) {
    e.preventDefault();
    if (filters.source_id.trim() && Number.isNaN(Number(filters.source_id.trim()))) {
      toast("来源 ID 必须是数字", "error");
      return;
    }
    setAppliedFilters(filters);
    void loadFirstPage(filters);
  }

  function handleReset() {
    setFilters(EMPTY_FILTERS);
    setAppliedFilters(EMPTY_FILTERS);
    void loadFirstPage(EMPTY_FILTERS);
  }

  function handleRefresh() {
    void loadFirstPage(appliedFilters);
  }

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">系统管理</p>
          <h1>打点日志</h1>
        </div>
        <div className="topActions">
          <button
            className="secondaryButton"
            type="button"
            disabled={loading}
            onClick={handleRefresh}
          >
            <RefreshCw size={15} />
            刷新
          </button>
        </div>
      </header>

      <form
        className="panel"
        style={{ marginBottom: 16, display: "flex", flexWrap: "wrap", gap: 12, alignItems: "flex-end" }}
        onSubmit={handleApplyFilters}
      >
        <label style={filterLabelStyle}>
          <span style={filterLabelTextStyle}>来源模块</span>
          <input
            type="text"
            value={filters.source_module}
            onChange={(e) => setFilters((f) => ({ ...f, source_module: e.target.value }))}
            placeholder="例如 goal_loop"
            style={inputStyle}
          />
        </label>
        <label style={filterLabelStyle}>
          <span style={filterLabelTextStyle}>来源类型</span>
          <input
            type="text"
            value={filters.source_type}
            onChange={(e) => setFilters((f) => ({ ...f, source_type: e.target.value }))}
            placeholder="例如 article"
            style={inputStyle}
          />
        </label>
        <label style={filterLabelStyle}>
          <span style={filterLabelTextStyle}>来源 ID</span>
          <input
            type="number"
            min={1}
            value={filters.source_id}
            onChange={(e) => setFilters((f) => ({ ...f, source_id: e.target.value }))}
            placeholder="例如 824"
            style={inputStyle}
          />
        </label>
        <label style={filterLabelStyle}>
          <span style={filterLabelTextStyle}>事件类型</span>
          <input
            type="text"
            value={filters.event_type}
            onChange={(e) => setFilters((f) => ({ ...f, event_type: e.target.value }))}
            placeholder="例如 writer_failed"
            style={inputStyle}
          />
        </label>
        <label style={filterLabelStyle}>
          <span style={filterLabelTextStyle}>级别</span>
          <select
            value={filters.level}
            onChange={(e) => setFilters((f) => ({ ...f, level: e.target.value }))}
            style={inputStyle}
          >
            <option value="">全部</option>
            <option value="info">info</option>
            <option value="warning">warning</option>
            <option value="error">error</option>
          </select>
        </label>
        <label style={filterLabelStyle}>
          <span style={filterLabelTextStyle}>开始时间</span>
          <input
            type="datetime-local"
            value={filters.start_at}
            onChange={(e) => setFilters((f) => ({ ...f, start_at: e.target.value }))}
            style={inputStyle}
          />
        </label>
        <label style={filterLabelStyle}>
          <span style={filterLabelTextStyle}>结束时间</span>
          <input
            type="datetime-local"
            value={filters.end_at}
            onChange={(e) => setFilters((f) => ({ ...f, end_at: e.target.value }))}
            style={inputStyle}
          />
        </label>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="primaryButton" type="submit" disabled={loading}>
            <Search size={15} />
            筛选
          </button>
          <button className="secondaryButton" type="button" onClick={handleReset} disabled={loading}>
            <RotateCcw size={15} />
            重置
          </button>
        </div>
      </form>

      <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
        {loading && items.length === 0 ? (
          <p style={{ padding: 24, color: "var(--fg-3)" }}>加载中…</p>
        ) : items.length === 0 ? (
          <p style={{ padding: 24, color: "var(--fg-3)" }}>暂无打点日志</p>
        ) : (
          <div style={{ overflow: "auto", maxHeight: "62vh" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={thStyle}>时间</th>
                  <th style={thStyle}>来源模块</th>
                  <th style={thStyle}>来源</th>
                  <th style={thStyle}>事件类型</th>
                  <th style={thStyle}>级别</th>
                  <th style={thStyle}>消息</th>
                  <th style={thStyle}>操作</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const sourceLabel =
                    it.source_type || it.source_id !== null
                      ? `${it.source_type ?? "—"}${it.source_id !== null ? `:${it.source_id}` : ""}`
                      : "—";
                  return (
                    <tr key={it.id} style={{ borderBottom: "1px solid var(--hair)", verticalAlign: "top" }}>
                      <td style={tdStyle}>
                        <span style={{ whiteSpace: "nowrap" }}>{formatDateTime(it.created_at)}</span>
                      </td>
                      <td style={tdStyle}>
                        <span style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }}>
                          {it.source_module}
                        </span>
                      </td>
                      <td style={tdStyle}>
                        <span style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }}>
                          {sourceLabel}
                        </span>
                      </td>
                      <td style={tdStyle}>
                        <span style={{ fontFamily: "var(--mono, monospace)", fontSize: 12 }}>
                          {it.event_type}
                        </span>
                      </td>
                      <td style={tdStyle}>
                        <span className={`badge ${levelBadgeClass(it.level)}`}>{it.level}</span>
                      </td>
                      <td style={tdStyle}>
                        <span title={it.message}>{truncate(it.message, 60)}</span>
                      </td>
                      <td style={tdStyle}>
                        <button
                          className="secondaryButton"
                          type="button"
                          style={{ padding: "4px 10px", fontSize: 12 }}
                          onClick={() => setDetailItem(it)}
                        >
                          详情
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        <div
          style={{
            display: "flex",
            justifyContent: "center",
            padding: 16,
            borderTop: items.length > 0 ? "1px solid var(--hair)" : undefined,
          }}
        >
          {nextCursor === null ? (
            <span style={{ color: "var(--fg-3)", fontSize: 13 }}>
              {items.length === 0 ? "" : "无更多"}
            </span>
          ) : (
            <button
              className="secondaryButton"
              type="button"
              disabled={loadingMore}
              onClick={() => void loadMore()}
            >
              {loadingMore ? "加载中…" : "加载更多"}
            </button>
          )}
        </div>
      </div>

      {detailItem && (
        <Modal
          title={`事件详情 #${detailItem.id}`}
          onClose={() => setDetailItem(null)}
          width={720}
          maxHeight="80vh"
          footer={
            <button className="secondaryButton" type="button" onClick={() => setDetailItem(null)}>
              关闭
            </button>
          }
        >
          <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13, padding: "18px 20px" }}>
            <DetailRow label="时间" value={formatDateTime(detailItem.created_at)} />
            <DetailRow label="来源模块" value={detailItem.source_module} />
            <DetailRow
              label="来源"
              value={
                detailItem.source_type || detailItem.source_id !== null
                  ? `${detailItem.source_type ?? "—"}${detailItem.source_id !== null ? `:${detailItem.source_id}` : ""}`
                  : "—"
              }
            />
            <DetailRow label="事件类型" value={detailItem.event_type} />
            <DetailRow
              label="级别"
              value={<span className={`badge ${levelBadgeClass(detailItem.level)}`}>{detailItem.level}</span>}
            />
            <div>
              <div style={{ color: "var(--fg-3)", fontWeight: 500, marginBottom: 4 }}>消息</div>
              <p style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{detailItem.message}</p>
            </div>
            <div>
              <div style={{ color: "var(--fg-3)", fontWeight: 500, marginBottom: 4 }}>Payload</div>
              {detailItem.payload_json === null || detailItem.payload_json === undefined ? (
                <span style={{ color: "var(--fg-3)" }}>—</span>
              ) : (
                <pre
                  style={{
                    margin: 0,
                    padding: 10,
                    background: "var(--glass)",
                    border: "1px solid var(--hair)",
                    borderRadius: 6,
                    fontSize: 12,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    maxHeight: "40vh",
                    overflow: "auto",
                  }}
                >
                  {JSON.stringify(detailItem.payload_json, null, 2)}
                </pre>
              )}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

function DetailRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "84px 1fr", gap: 8, alignItems: "baseline" }}>
      <span style={{ color: "var(--fg-3)", fontWeight: 500, whiteSpace: "nowrap", overflow: "hidden" }}>
        {label}
      </span>
      <span>{value}</span>
    </div>
  );
}

const thStyle: React.CSSProperties = {
  padding: "10px 16px",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--fg-3)",
  fontSize: 12,
  textTransform: "uppercase",
  letterSpacing: "0.04em",
  whiteSpace: "nowrap",
  position: "sticky",
  top: 0,
  zIndex: 1,
  background: "var(--surface-2)",
  boxShadow: "inset 0 -1px 0 var(--hair)",
};

const tdStyle: React.CSSProperties = {
  padding: "12px 16px",
  verticalAlign: "top",
};

const filterLabelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  fontSize: 12,
};

const filterLabelTextStyle: React.CSSProperties = {
  color: "var(--fg-3)",
  fontWeight: 500,
};

const inputStyle: React.CSSProperties = {
  padding: "6px 10px",
  border: "1px solid var(--hair)",
  borderRadius: 6,
  fontSize: 13,
  background: "var(--glass)",
  color: "var(--fg)",
  colorScheme: "dark",
  minWidth: 160,
};
