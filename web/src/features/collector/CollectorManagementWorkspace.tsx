import { useEffect, useState } from "react";
import { Activity, Box, Database, RefreshCw, ServerCog } from "lucide-react";
import {
  getCollectorBacklog,
  getCollectorRun,
  getCollectorTransfer,
  listCollectorNodes,
  listCollectorRuns,
} from "../../api/collector-management";
import type {
  CollectorBacklogView,
  CollectorNodeView,
  CollectorRunEvent,
  CollectorRunSummary,
  CollectorRunView,
  CollectorTransferView,
} from "../../api/collector-management";
import { useToast } from "../../components/Toast";
import { formatDateTime } from "../../utils/dateFormat";
import { nodeHistoryEntries, transferReceiptEvidence } from "./collectorManagementViewModel";

function freshnessLabel(freshness: CollectorNodeView["freshness"]): string {
  return { online: "在线", stale: "离线 / 心跳过期", never_seen: "尚未心跳" }[freshness];
}

function statusBadge(status: string): string {
  if (status === "online" || status === "processed" || status === "succeeded") return "succeeded";
  if (status === "stale" || status === "failed" || status === "dead_letter") return "failed";
  if (status === "processing" || status === "running") return "running";
  return "pending";
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function correlationValue(value: string | null | undefined): string {
  return value || "—";
}

function JsonDetail({ value }: { value: Record<string, unknown> | null }) {
  if (!value || Object.keys(value).length === 0)
    return <span style={{ color: "var(--fg-3)" }}>—</span>;
  return (
    <details>
      <summary style={{ cursor: "pointer", color: "var(--accent-deep, #0369a1)", fontSize: 12 }}>
        查看已脱敏详情
      </summary>
      <pre style={jsonStyle}>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function EventRow({
  event,
  onOpenTransfer,
}: {
  event: CollectorRunEvent;
  onOpenTransfer: (id: string) => void;
}) {
  return (
    <li style={timelineItemStyle}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
          <span className={`badge ${statusBadge(event.level)}`}>{event.level}</span>
          <strong>{event.event_type}</strong>
          <span style={{ color: "var(--fg-3)" }}>{event.stage}</span>
        </div>
        <time style={{ color: "var(--fg-3)", fontSize: 12 }}>
          {formatDateTime(event.occurred_at)}
        </time>
      </div>
      <div style={correlationStyle}>
        <span>来源：{correlationValue(event.source)}</span>
        <span>
          组件：{event.component}@{event.component_version}
        </span>
        <span>Bundle：{correlationValue(event.bundle_id)}</span>
        {event.transport_id ? (
          <button
            className="secondaryButton"
            style={compactButtonStyle}
            type="button"
            onClick={() => onOpenTransfer(event.transport_id!)}
          >
            传输 {event.transport_id}
          </button>
        ) : null}
      </div>
      <JsonDetail value={event.payload} />
    </li>
  );
}

export function CollectorManagementWorkspace() {
  const { toast } = useToast();
  const [nodes, setNodes] = useState<CollectorNodeView[]>([]);
  const [backlog, setBacklog] = useState<CollectorBacklogView | null>(null);
  const [runs, setRuns] = useState<CollectorRunSummary[]>([]);
  const [run, setRun] = useState<CollectorRunView | null>(null);
  const [transfer, setTransfer] = useState<CollectorTransferView | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);

  async function loadOverview() {
    setLoading(true);
    try {
      const [nodeData, backlogData, runData] = await Promise.all([
        listCollectorNodes(),
        getCollectorBacklog(),
        listCollectorRuns(),
      ]);
      setNodes(nodeData.items);
      setBacklog(backlogData);
      setRuns(runData.items);
    } catch (error) {
      toast(error instanceof Error ? error.message : "加载 Collector 概览失败", "error");
    } finally {
      setLoading(false);
    }
  }

  async function openRun(runId: string) {
    setDetailLoading(true);
    setTransfer(null);
    try {
      setRun(await getCollectorRun(runId));
    } catch (error) {
      toast(error instanceof Error ? error.message : "加载运行时间线失败", "error");
    } finally {
      setDetailLoading(false);
    }
  }

  async function openTransfer(transportId: string) {
    setDetailLoading(true);
    try {
      setTransfer(await getCollectorTransfer(transportId));
    } catch (error) {
      toast(error instanceof Error ? error.message : "加载传输详情失败", "error");
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => {
    void loadOverview();
    // 初始概览仅在挂载时请求；刷新按钮始终调用当前的函数闭包。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const counts = backlog?.counts;
  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Collector 管理</p>
          <h1>节点、传输与导入</h1>
        </div>
        <div className="topActions">
          <button
            className="secondaryButton"
            type="button"
            disabled={loading}
            onClick={() => void loadOverview()}
          >
            <RefreshCw size={15} />
            刷新只读数据
          </button>
        </div>
      </header>

      <section style={sectionStyle} aria-label="传输积压">
        <h2 style={sectionTitleStyle}>
          <Box size={18} /> 传输积压
        </h2>
        <div className="statGrid">
          <dt>就绪</dt>
          <dd>{counts?.ready ?? "—"}</dd>
          <dt>处理中</dt>
          <dd>{counts?.processing ?? "—"}</dd>
          <dt>重试等待</dt>
          <dd>{counts?.retry_wait ?? "—"}</dd>
          <dt>失败 / 死信</dt>
          <dd>{counts ? `${counts.failed} / ${counts.dead_letter}` : "—"}</dd>
          <dt>最早就绪</dt>
          <dd>{formatDateTime(backlog?.oldest_ready_at)}</dd>
        </div>
      </section>

      <section
        className="panel"
        style={{ marginBottom: 16, overflow: "hidden", padding: 0 }}
        aria-label="最近运行"
      >
        <div
          style={{
            padding: "16px 18px",
            borderBottom: "1px solid var(--hair)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <Activity size={18} />
          <h2 style={{ margin: 0, fontSize: 16 }}>最近运行</h2>
        </div>
        {runs.length === 0 ? (
          <p style={{ padding: 20, color: "var(--fg-3)" }}>尚无 Collector 运行记录。</p>
        ) : (
          <div style={{ overflow: "auto" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>运行 / Job</th>
                  <th style={thStyle}>节点</th>
                  <th style={thStyle}>状态</th>
                  <th style={thStyle}>开始 / 完成</th>
                  <th style={thStyle}>详情</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((item) => (
                  <tr key={item.run_id} style={rowStyle}>
                    <td style={tdStyle}>
                      <span style={monoStyle}>{item.run_id}</span>
                      <br />
                      <small style={monoStyle}>{item.job_id}</small>
                    </td>
                    <td style={tdStyle}>{item.collector_id}</td>
                    <td style={tdStyle}>
                      <span className={`badge ${statusBadge(item.status)}`}>{item.status}</span>
                    </td>
                    <td style={tdStyle}>
                      {formatDateTime(item.started_at)}
                      <br />
                      {formatDateTime(item.finished_at)}
                    </td>
                    <td style={tdStyle}>
                      <button
                        className="secondaryButton"
                        style={compactButtonStyle}
                        type="button"
                        onClick={() => void openRun(item.run_id)}
                      >
                        查看时间线
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section
        className="panel"
        style={{ marginBottom: 16, overflow: "hidden", padding: 0 }}
        aria-label="Collector 节点"
      >
        <div
          style={{
            padding: "16px 18px",
            borderBottom: "1px solid var(--hair)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <ServerCog size={18} />
          <h2 style={{ margin: 0, fontSize: 16 }}>Collector 节点</h2>
        </div>
        {loading ? (
          <p style={{ padding: 20, color: "var(--fg-3)" }}>加载中…</p>
        ) : nodes.length === 0 ? (
          <p style={{ padding: 20, color: "var(--fg-3)" }}>尚无已注册 Collector 节点。</p>
        ) : (
          <div style={{ overflow: "auto" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>节点</th>
                  <th style={thStyle}>状态</th>
                  <th style={thStyle}>当前阶段</th>
                  <th style={thStyle}>来源</th>
                  <th style={thStyle}>Spool</th>
                  <th style={thStyle}>最后心跳</th>
                  <th style={thStyle}>运行</th>
                </tr>
              </thead>
              <tbody>
                {nodes.map((node) => (
                  <tr key={node.collector_id} style={rowStyle}>
                    <td style={tdStyle}>
                      <strong>{node.display_name}</strong>
                      <br />
                      <span style={monoStyle}>{node.collector_id}</span>
                      <br />
                      <small>
                        {node.platform ?? "未知平台"} · {node.agent_version ?? "未知版本"} ·{" "}
                        {node.destination}
                      </small>
                    </td>
                    <td style={tdStyle}>
                      <span className={`badge ${statusBadge(node.freshness)}`}>
                        {freshnessLabel(node.freshness)}
                      </span>
                      <br />
                      <small>{node.status}</small>
                    </td>
                    <td style={tdStyle}>{node.current_stage ?? "空闲"}</td>
                    <td style={tdStyle}>
                      {node.enabled_sources.length ? node.enabled_sources.join("、") : "—"}
                    </td>
                    <td style={tdStyle}>{node.spool_pending_count}</td>
                    <td style={tdStyle}>
                      {formatDateTime(node.last_heartbeat_at)}
                      {nodeHistoryEntries(node).map((entry) => (
                        <small key={entry.label} style={{ display: "block" }}>
                          {entry.label}：{formatDateTime(entry.timestamp)}
                        </small>
                      ))}
                      {node.last_error_summary ? (
                        <>
                          <br />
                          <small style={{ color: "var(--red)" }}>{node.last_error_summary}</small>
                        </>
                      ) : null}
                    </td>
                    <td style={tdStyle}>
                      {node.current_run_id ? (
                        <button
                          className="secondaryButton"
                          style={compactButtonStyle}
                          type="button"
                          onClick={() => void openRun(node.current_run_id!)}
                        >
                          查看时间线
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {detailLoading ? <p style={{ color: "var(--fg-3)" }}>加载详情…</p> : null}
      {run ? (
        <section className="panel" style={sectionStyle} aria-label="运行时间线">
          <h2 style={sectionTitleStyle}>
            <Activity size={18} /> 运行时间线
          </h2>
          <div className="statGrid" style={{ marginBottom: 16 }}>
            <dt>运行 ID</dt>
            <dd style={monoStyle}>{run.run.run_id}</dd>
            <dt>Collector / Job</dt>
            <dd style={monoStyle}>
              {run.run.collector_id} / {run.run.job_id}
            </dd>
            <dt>状态 / 阶段</dt>
            <dd>
              <span className={`badge ${statusBadge(run.run.status)}`}>{run.run.status}</span>{" "}
              {run.run.current_stage ?? "—"}
            </dd>
            <dt>开始 / 完成</dt>
            <dd>
              {formatDateTime(run.run.started_at)} / {formatDateTime(run.run.finished_at)}
            </dd>
            <dt>错误</dt>
            <dd>
              {run.run.error_classification ?? "—"} {run.run.error_summary ?? ""}
            </dd>
          </div>
          <JsonDetail value={run.run.summary} />
          {run.events.length === 0 ? (
            <p style={{ color: "var(--fg-3)" }}>此运行尚无事件。</p>
          ) : (
            <ol style={timelineStyle}>
              {run.events.map((event) => (
                <EventRow
                  key={event.event_id}
                  event={event}
                  onOpenTransfer={(id) => void openTransfer(id)}
                />
              ))}
            </ol>
          )}
        </section>
      ) : null}

      {transfer ? (
        <section className="panel" style={sectionStyle} aria-label="传输与导入详情">
          <h2 style={sectionTitleStyle}>
            <Database size={18} /> 传输与导入
          </h2>
          <div className="statGrid" style={{ marginBottom: 16 }}>
            <dt>传输 ID</dt>
            <dd style={monoStyle}>{transfer.transfer.transport_id}</dd>
            <dt>Bundle / 运行</dt>
            <dd style={monoStyle}>
              {transfer.transfer.bundle_id} / {transfer.transfer.run_id}
            </dd>
            <dt>状态 / 尝试</dt>
            <dd>
              <span className={`badge ${statusBadge(transfer.transfer.status)}`}>
                {transfer.transfer.status}
              </span>{" "}
              {transfer.transfer.attempt_count}
            </dd>
            <dt>Archive</dt>
            <dd>
              {formatBytes(transfer.transfer.archive_size)} ·{" "}
              <span style={monoStyle}>{transfer.transfer.archive_sha256}</span>
            </dd>
            <dt>就绪 / 已处理</dt>
            <dd>
              {formatDateTime(transfer.transfer.ready_at)} /{" "}
              {formatDateTime(transfer.transfer.processed_at)}
            </dd>
            <dt>错误</dt>
            <dd>
              {transfer.transfer.error_classification ?? "—"}{" "}
              {transfer.transfer.error_summary ?? ""}
            </dd>
          </div>
          {(() => {
            const receipt = transferReceiptEvidence(transfer);
            if (!receipt) return <p style={{ color: "var(--fg-3)" }}>尚未生成 Consumer 回执。</p>;
            return (
              <div style={{ marginBottom: 16, color: "var(--fg-2)" }}>
                <p style={{ margin: "0 0 6px" }}>
                  回执：<strong>{receipt.status}</strong>，完成项目 {receipt.completedItemCount}/
                  {receipt.itemCount}，完成时间 {formatDateTime(receipt.completedAt)}
                </p>
                <p style={{ margin: 0 }}>
                  失败分类：{receipt.errorClassification ?? "—"}
                  {receipt.errorSummary ? ` · ${receipt.errorSummary}` : ""}
                </p>
                <p style={{ margin: "6px 0 0" }}>
                  回放证据：
                  <span style={monoStyle}>{receipt.replayEvidenceRef ?? "—"}</span>
                </p>
              </div>
            );
          })()}
          <div style={{ overflow: "auto" }}>
            <table style={tableStyle}>
              <thead>
                <tr>
                  <th style={thStyle}>Item</th>
                  <th style={thStyle}>来源</th>
                  <th style={thStyle}>状态</th>
                  <th style={thStyle}>GEO 游戏</th>
                  <th style={thStyle}>完成</th>
                  <th style={thStyle}>结果</th>
                </tr>
              </thead>
              <tbody>
                {transfer.items.map((item) => (
                  <tr key={item.item_key} style={rowStyle}>
                    <td style={tdStyle}>
                      <span style={monoStyle}>{item.item_key}</span>
                    </td>
                    <td style={tdStyle}>
                      {item.source}
                      <br />
                      <small>{item.source_item_id ?? "—"}</small>
                    </td>
                    <td style={tdStyle}>
                      <span className={`badge ${statusBadge(item.status)}`}>{item.status}</span>
                      {item.error_summary ? (
                        <small style={{ display: "block", color: "var(--red)" }}>
                          {item.error_summary}
                        </small>
                      ) : null}
                    </td>
                    <td style={tdStyle}>{item.game_id ?? "—"}</td>
                    <td style={tdStyle}>{formatDateTime(item.completed_at)}</td>
                    <td style={tdStyle}>
                      <JsonDetail value={item.outcome} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}
    </>
  );
}

const sectionStyle = { marginBottom: 16 };
const sectionTitleStyle = {
  margin: "0 0 16px",
  display: "flex",
  alignItems: "center",
  gap: 8,
  fontSize: 16,
};
const tableStyle = { width: "100%", borderCollapse: "collapse" as const, fontSize: 13 };
const thStyle = {
  ...tableStyle,
  width: undefined,
  padding: "10px 14px",
  textAlign: "left" as const,
  fontWeight: 600,
  color: "var(--fg-3)",
  fontSize: 12,
  whiteSpace: "nowrap" as const,
  background: "var(--surface-2)",
};
const tdStyle = { padding: "12px 14px", verticalAlign: "top" as const };
const rowStyle = { borderBottom: "1px solid var(--hair)" };
const monoStyle = {
  fontFamily: "var(--mono, monospace)",
  fontSize: 12,
  wordBreak: "break-all" as const,
};
const compactButtonStyle = { padding: "4px 9px", fontSize: 12 };
const correlationStyle = {
  display: "flex",
  gap: 10,
  flexWrap: "wrap" as const,
  margin: "7px 0",
  color: "var(--fg-3)",
  fontSize: 12,
};
const timelineStyle = {
  listStyle: "none",
  margin: "16px 0 0",
  padding: 0,
  display: "grid",
  gap: 10,
};
const timelineItemStyle = {
  borderLeft: "3px solid var(--accent, #0ea5e9)",
  padding: "10px 12px",
  background: "var(--glass)",
  borderRadius: "0 var(--r) var(--r) 0",
};
const jsonStyle = {
  margin: "7px 0 0",
  padding: 10,
  background: "var(--glass)",
  border: "1px solid var(--hair)",
  borderRadius: 6,
  fontSize: 12,
  whiteSpace: "pre-wrap" as const,
  wordBreak: "break-word" as const,
  maxHeight: 240,
  overflow: "auto" as const,
};
