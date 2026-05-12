import { useEffect, useMemo, useRef, useState } from "react";
import { api, newClientRequestId, singleFlight } from "../../api/client";
import type { Task, Account, ArticleGroup, ArticleSummary, PublishRecord, TaskLog, AssignmentPreview } from "../../types";
import { TERMINAL_STATUSES, statusLabel } from "../../types";
import { Plus, RefreshCw, Send } from "lucide-react";

const TASK_PAGE_SIZE = 10;

export function TasksWorkspace() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskPage, setTaskPage] = useState(0);
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const [records, setRecords] = useState<PublishRecord[]>([]);
  const [logs, setLogs] = useState<TaskLog[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [articles, setArticles] = useState<ArticleSummary[]>([]);
  const [groups, setGroups] = useState<ArticleGroup[]>([]);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [autoRefreshTaskIds, setAutoRefreshTaskIds] = useState<Set<number>>(new Set());

  const [formName, setFormName] = useState("");
  const [formType, setFormType] = useState<"single" | "group_round_robin">("single");
  const [formArticleId, setFormArticleId] = useState<number | null>(null);
  const [formGroupId, setFormGroupId] = useState<number | null>(null);
  const [formAccountIds, setFormAccountIds] = useState<number[]>([]);
  const [preview, setPreview] = useState<AssignmentPreview | null>(null);
  const [formError, setFormError] = useState("");

  const lastLogIdRef = useRef(0);

  const selectedTask = tasks.find((t) => t.id === selectedTaskId) ?? null;
  const hasActiveRecords = records.some(r =>
    r.status === "running" || r.status === "waiting_user_input" || r.status === "waiting_manual_publish"
  );
  const shouldPollSelectedTask =
    selectedTaskId !== null &&
    (selectedTask?.status === "running" || hasActiveRecords || autoRefreshTaskIds.has(selectedTaskId));
  const articleMap = useMemo(() => Object.fromEntries(articles.map((a) => [a.id, a])), [articles]);
  const accountMap = useMemo(() => Object.fromEntries(accounts.map((a) => [a.id, a])), [accounts]);
  const sortedTasks = useMemo(
    () => tasks.slice().sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()),
    [tasks],
  );
  const totalTaskPages = Math.max(1, Math.ceil(sortedTasks.length / TASK_PAGE_SIZE));
  const pagedTasks = sortedTasks.slice(taskPage * TASK_PAGE_SIZE, (taskPage + 1) * TASK_PAGE_SIZE);

  useEffect(() => {
    void loadInitial();
  }, []);

  useEffect(() => {
    if (!selectedTaskId || !shouldPollSelectedTask) return;
    const interval = setInterval(() => {
      void refreshDetail(selectedTaskId);
    }, 2500);
    return () => clearInterval(interval);
  }, [selectedTaskId, shouldPollSelectedTask]);

  useEffect(() => {
    if (taskPage >= totalTaskPages) {
      setTaskPage(totalTaskPages - 1);
    }
  }, [taskPage, totalTaskPages]);

  async function loadInitial() {
    const [ts, accs, arts, gs] = await Promise.all([
      api<Task[]>("/api/tasks"),
      api<Account[]>("/api/accounts"),
      api<ArticleSummary[]>("/api/articles"),
      api<ArticleGroup[]>("/api/article-groups"),
    ]);
    setTasks(ts);
    setAccounts(accs);
    setArticles(arts);
    setGroups(gs);
  }

  async function refreshDetail(taskId: number) {
    const [rs, ls, ts] = await Promise.all([
      api<PublishRecord[]>(`/api/tasks/${taskId}/records`),
      api<TaskLog[]>(`/api/tasks/${taskId}/logs?after_id=${lastLogIdRef.current}`),
      api<Task[]>("/api/tasks"),
    ]);
    setRecords(rs);
    if (ls.length > 0) {
      setLogs((prev) => [...prev, ...ls]);
      lastLogIdRef.current = Math.max(...ls.map((l) => l.id));
    }
    setTasks(ts);
    const currentTask = ts.find((task) => task.id === taskId);
    if (!currentTask || TERMINAL_STATUSES.has(currentTask.status)) {
      setAutoRefreshTaskIds((prev) => {
        if (!prev.has(taskId)) return prev;
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  }

  async function selectTask(taskId: number) {
    setSelectedTaskId(taskId);
    lastLogIdRef.current = 0;
    setLogs([]);
    await refreshDetail(taskId);
  }

  async function createTask() {
    setFormError("");
    if (!formName.trim() || formAccountIds.length === 0) {
      setFormError("请填写任务名称并选择账号");
      return;
    }
    if (formType === "single" && !formArticleId) {
      setFormError("请选择文章");
      return;
    }
    if (formType === "group_round_robin" && !formGroupId) {
      setFormError("请选择分组");
      return;
    }
    setLoading(true);
    try {
      const task = await singleFlight("task-create", () =>
        api<Task>("/api/tasks", {
          method: "POST",
          body: JSON.stringify({
            name: formName.trim(),
            client_request_id: newClientRequestId("task"),
            task_type: formType,
            article_id: formType === "single" ? formArticleId : null,
            group_id: formType === "group_round_robin" ? formGroupId : null,
            accounts: formAccountIds.map((id, index) => ({ account_id: id, sort_order: index })),
            stop_before_publish: false,
          }),
        }),
      );
      if (!task) return;
      setShowCreateForm(false);
      setFormName("");
      setFormArticleId(null);
      setFormGroupId(null);
      setFormAccountIds([]);
      setFormError("");
      setPreview(null);
      setTaskPage(0);
      setNotice("任务已创建");
      await selectTask(task.id);
    } catch (error) {
      const msg = error instanceof Error ? error.message : "创建失败";
      setNotice(msg);
      setFormError(msg);
    } finally {
      setLoading(false);
    }
  }

  async function loadPreview() {
    if (!formGroupId || formAccountIds.length === 0) return;
    setLoading(true);
    try {
      const result = await api<AssignmentPreview>("/api/tasks/preview", {
        method: "POST",
        body: JSON.stringify({
          name: formName || "预览",
          task_type: "group_round_robin",
          group_id: formGroupId,
          accounts: formAccountIds.map((id, index) => ({ account_id: id, sort_order: index })),
          stop_before_publish: false,
        }),
      });
      setPreview(result);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "预览失败");
    } finally {
      setLoading(false);
    }
  }

  async function executeTask() {
    if (!selectedTaskId) return;
    setLoading(true);
    try {
      setAutoRefreshTaskIds((prev) => new Set(prev).add(selectedTaskId));
      await singleFlight(`task-execute-${selectedTaskId}`, () =>
        api<Task>(`/api/tasks/${selectedTaskId}/execute`, { method: "POST" }),
      );
      await refreshDetail(selectedTaskId);
      setNotice("已启动");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "启动失败");
    } finally {
      setLoading(false);
    }
  }

  async function cancelTask() {
    if (!selectedTaskId) return;
    setLoading(true);
    try {
      await singleFlight(`task-cancel-${selectedTaskId}`, () =>
        api<Task>(`/api/tasks/${selectedTaskId}/cancel`, { method: "POST" }),
      );
      setAutoRefreshTaskIds((prev) => {
        if (!prev.has(selectedTaskId)) return prev;
        const next = new Set(prev);
        next.delete(selectedTaskId);
        return next;
      });
      await refreshDetail(selectedTaskId);
      setNotice("已取消");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "取消失败");
    } finally {
      setLoading(false);
    }
  }

  async function retryRecord(recordId: number) {
    setLoading(true);
    try {
      await singleFlight(`record-retry-${recordId}`, () =>
        api<PublishRecord>(`/api/publish-records/${recordId}/retry`, { method: "POST" }),
      );
      if (selectedTaskId) {
        setAutoRefreshTaskIds((prev) => new Set(prev).add(selectedTaskId));
        await singleFlight(`task-execute-${selectedTaskId}`, () =>
          api<Task>(`/api/tasks/${selectedTaskId}/execute`, { method: "POST" }),
        );
        await refreshDetail(selectedTaskId);
      }
      setNotice("重试已启动");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "重试失败");
    } finally {
      setLoading(false);
    }
  }

  function toggleAccount(accountId: number) {
    if (formType === "single") {
      setFormAccountIds([accountId]);
    } else {
      setFormAccountIds((prev) =>
        prev.includes(accountId) ? prev.filter((id) => id !== accountId) : [...prev, accountId],
      );
    }
    setPreview(null);
  }

  const validAccounts = accounts.filter((a) => a.status === "valid");
  const canExecute = selectedTask && selectedTask.status === "pending";
  const canCancel = selectedTask && (selectedTask.status === "running" || selectedTask.status === "pending");

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">分发引擎</p>
          <h1>任务管理</h1>
        </div>
        <div className="topActions">
          {notice ? <span className="status">{notice}</span> : null}
        </div>
      </header>

      <section className="taskGrid">
        <div className="listPane">
          <button
            className="primaryButton"
            style={{ width: "100%", marginBottom: 12 }}
            type="button"
            onClick={() => { setShowCreateForm((v) => !v); setPreview(null); }}
          >
            <Plus size={16} />
            {showCreateForm ? "收起" : "创建任务"}
          </button>

          {showCreateForm ? (
            <div className="createForm">
              <label>
                任务名称
                <input value={formName} placeholder="例如：头条号5月第一批" onChange={(e) => setFormName(e.target.value)} />
              </label>
              <label>
                任务类型
                <select
                  value={formType}
                  onChange={(e) => { setFormType(e.target.value as "single" | "group_round_robin"); setPreview(null); }}
                >
                  <option value="single">单篇发布</option>
                  <option value="group_round_robin">分组轮询</option>
                </select>
              </label>
              {formType === "single" ? (
                <label>
                  文章
                  <select
                    value={formArticleId ?? ""}
                    onChange={(e) => setFormArticleId(Number(e.target.value) || null)}
                  >
                    <option value="">请选择文章</option>
                    {articles.map((a) => (
                      <option key={a.id} value={a.id}>{a.title}</option>
                    ))}
                  </select>
                </label>
              ) : (
                <label>
                  文章分组
                  <select
                    value={formGroupId ?? ""}
                    onChange={(e) => { setFormGroupId(Number(e.target.value) || null); setPreview(null); }}
                  >
                    <option value="">请选择分组</option>
                    {groups.map((g) => (
                      <option key={g.id} value={g.id}>{g.name}（{g.items.length} 篇）</option>
                    ))}
                  </select>
                </label>
              )}
              <div>
                <p style={{ margin: "0 0 6px", fontSize: 13, color: "#475569" }}>发布账号</p>
                {formType === "single" ? <p style={{ margin: "0 0 6px", fontSize: 12, color: "#e67e22" }}>单篇发布只能选一个账号</p> : null}
                {validAccounts.map((a) => (
                  <label key={a.id} className="checkLine">
                    <input type={formType === "single" ? "radio" : "checkbox"} name="formAccount" checked={formAccountIds.includes(a.id)} onChange={() => toggleAccount(a.id)} />
                    <span>{a.display_name}</span>
                  </label>
                ))}
                {validAccounts.length === 0 ? <p className="emptyText">暂无有效账号</p> : null}
              </div>
              {formType === "group_round_robin" && formGroupId && formAccountIds.length > 0 ? (
                <button className="secondaryButton" style={{ width: "100%" }} type="button" disabled={loading} onClick={() => void loadPreview()}>
                  预览分配
                </button>
              ) : null}
              {preview ? (
                <div className="previewBox">
                  <p style={{ margin: "0 0 6px", fontSize: 13, color: "#475569" }}>
                    {preview.article_count} 篇 · {preview.account_count} 个账号
                  </p>
                  {preview.items.map((item) => (
                    <div key={item.position} className="previewRow">
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {articleMap[item.article_id]?.title ?? `文章 ${item.article_id}`}
                      </span>
                      <span style={{ flexShrink: 0, color: "#64748b" }}>
                        {accountMap[item.account_id]?.display_name ?? `账号 ${item.account_id}`}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
              {formError ? (
                <div style={{ padding: "8px 12px", background: "var(--red-soft)", color: "var(--red)", borderRadius: "var(--r)", fontSize: 13 }}>
                  {formError}
                </div>
              ) : null}
              <button className="primaryButton" style={{ width: "100%" }} type="button" disabled={loading} onClick={() => void createTask()}>
                创建任务
              </button>
            </div>
          ) : null}

          <div className="articleList taskList">
            {pagedTasks.map((task) => (
              <button
                key={task.id}
                className={`taskItem ${task.id === selectedTaskId ? "selected" : ""}`}
                type="button"
                onClick={() => void selectTask(task.id)}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                    {task.name}
                  </strong>
                  <span className={`badge ${task.status}`}>{statusLabel(task.status)}</span>
                </div>
                <small style={{ color: "#64748b", fontSize: 12 }}>
                  {task.task_type === "single" ? "单篇" : "分组轮询"} · {task.record_count} 条 · {new Date(task.created_at).toLocaleDateString()}
                </small>
              </button>
            ))}
            {tasks.length === 0 ? <p className="emptyText">暂无任务</p> : null}
          </div>
          <div className="pagerRow">
            <button type="button" disabled={taskPage === 0 || loading} onClick={() => setTaskPage((page) => Math.max(0, page - 1))}>
              上一页
            </button>
            <span>第 {taskPage + 1} / {totalTaskPages} 页</span>
            <button
              type="button"
              disabled={taskPage >= totalTaskPages - 1 || loading}
              onClick={() => setTaskPage((page) => Math.min(totalTaskPages - 1, page + 1))}
            >
              下一页
            </button>
          </div>
        </div>

        {selectedTask ? (
          <div className="taskDetail">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 14 }}>
              <div>
                <h2 style={{ margin: "0 0 4px" }}>{selectedTask.name}</h2>
                <small style={{ color: "#64748b", fontSize: 13 }}>
                  {selectedTask.task_type === "single" ? "单篇发布" : "分组轮询"} · 头条号
                  {selectedTask.started_at ? ` · 开始于 ${new Date(selectedTask.started_at).toLocaleString()}` : ""}
                </small>
              </div>
              <span className={`badge ${selectedTask.status}`}>{statusLabel(selectedTask.status)}</span>
            </div>

            <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
              {canExecute ? (
                <button className="primaryButton" type="button" disabled={loading} onClick={() => void executeTask()}>
                  <Send size={15} />
                  执行
                </button>
              ) : null}
              {canCancel ? (
                <button className="dangerButton" type="button" disabled={loading} onClick={() => void cancelTask()}>
                  取消任务
                </button>
              ) : null}
            </div>

            <hr className="sectionDivider" />
            <h2 style={{ marginBottom: 12 }}>发布记录</h2>
            <div style={{ display: "grid", gap: 10, marginBottom: 20 }}>
              {records.map((record) => {
                const article = articleMap[record.article_id];
                const account = accountMap[record.account_id];
                return (
                  <div key={record.id} className="recordItem">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                        {article?.title ?? `文章 ${record.article_id}`}
                      </span>
                      <span className={`badge ${record.status}`}>{statusLabel(record.status)}</span>
                    </div>
                    <small style={{ color: "#64748b", fontSize: 13 }}>
                      {account?.display_name ?? `账号 ${record.account_id}`}
                      {record.retry_of_record_id ? ` · 重试自 #${record.retry_of_record_id}` : ""}
                    </small>
                    {record.publish_url ? (
                      <small>
                        <a href={record.publish_url} target="_blank" rel="noreferrer" style={{ color: "#214f7a" }}>
                          查看已发布链接
                        </a>
                      </small>
                    ) : null}
                    {record.error_message ? (
                      <small style={{ color: "#dc2626" }}>{record.error_message}</small>
                    ) : null}

                    {record.status === "failed" && !records.some((r) => r.retry_of_record_id === record.id) ? (
                      <button
                        className="secondaryButton"
                        type="button"
                        disabled={loading}
                        style={{ justifySelf: "start" }}
                        onClick={() => void retryRecord(record.id)}
                      >
                        <RefreshCw size={13} />
                        重试
                      </button>
                    ) : null}
                    {record.status === "waiting_user_input" && record.novnc_url ? (
                      <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 8 }}>
                        <button
                          className="primaryButton"
                          type="button"
                          onClick={() => window.open(record.novnc_url!, "_blank")}
                        >
                          打开远程浏览器
                        </button>
                        <small style={{ color: "#64748b" }}>
                          浏览器会话将在空闲 5 分钟后自动关闭
                        </small>
                      </div>
                    ) : null}
                  </div>
                );
              })}
              {records.length === 0 ? <p className="emptyText">暂无发布记录</p> : null}
            </div>

            <hr className="sectionDivider" />
            <h2 style={{ marginBottom: 12 }}>执行日志</h2>
            <div className="logList">
              {logs.map((log) => (
                <div key={log.id} className="logItem">
                  <span className={`logLevel ${log.level}`}>{log.level.toUpperCase()}</span>
                  <span style={{ flex: 1 }}>{log.message}</span>
                  <small style={{ color: "#94a3b8", flexShrink: 0 }}>
                    {new Date(log.created_at).toLocaleTimeString()}
                  </small>
                </div>
              ))}
              {logs.length === 0 ? <p className="emptyText" style={{ margin: "6px 0" }}>暂无日志</p> : null}
            </div>
          </div>
        ) : (
          <div className="taskDetail" style={{ display: "grid", placeItems: "center", minHeight: 260, color: "#94a3b8" }}>
            <p style={{ margin: 0 }}>选择左侧任务查看详情</p>
          </div>
        )}
      </section>
    </>
  );
}
