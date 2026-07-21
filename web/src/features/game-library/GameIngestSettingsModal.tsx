import { useState } from "react";
import { importImageCategories, patchIngestConfig, startIngestRun } from "../../api/game-library";
import type { GameIngestConfig } from "../../types";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";

type Props = {
  config: GameIngestConfig;
  onClose: () => void;
  onSaved: (cfg: GameIngestConfig) => void;
  onImported?: () => void;
};

function summarizeRun(summary: Record<string, unknown> | null): string {
  if (!summary) return "";
  try {
    const json = JSON.stringify(summary);
    return json.length > 120 ? `${json.slice(0, 120)}…` : json;
  } catch {
    return "";
  }
}

export function GameIngestSettingsModal({ config, onClose, onSaved, onImported }: Props) {
  const { toast } = useToast();
  const [form, setForm] = useState({
    enabled: config.enabled,
    window_start: config.window_start,
    window_end: config.window_end,
    batch_size: config.batch_size,
    min_gap_seconds: config.min_gap_seconds,
    max_gap_seconds: config.max_gap_seconds,
    source_order: config.source_order,
    max_shots: config.max_shots,
  });
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(config.running);
  const [runStarting, setRunStarting] = useState(false);
  const [importing, setImporting] = useState(false);
  const [lastFinishedAt, setLastFinishedAt] = useState(config.last_run_finished_at);
  const [lastSummary, setLastSummary] = useState(config.last_run_summary);

  function setField<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const next = await patchIngestConfig({
        enabled: form.enabled,
        window_start: form.window_start.trim(),
        window_end: form.window_end.trim(),
        batch_size: form.batch_size,
        min_gap_seconds: form.min_gap_seconds,
        max_gap_seconds: form.max_gap_seconds,
        source_order: form.source_order.trim(),
        max_shots: form.max_shots,
      });
      onSaved(next);
      toast("已保存抓取配置", "success");
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存抓取配置失败", "error");
    } finally {
      setSaving(false);
    }
  }

  async function handleRunNow() {
    if (runStarting || running) return;
    setRunStarting(true);
    try {
      const res = await startIngestRun();
      setRunning(res.status.running);
      setLastFinishedAt(res.status.last_run_finished_at);
      setLastSummary(res.status.last_run_summary);
      toast(res.started ? "已开始抓取一批" : "抓取任务已在进行中，请稍后再试", res.started ? "success" : "error");
    } catch (e) {
      const msg = e instanceof Error ? e.message : "触发抓取失败";
      // core.ts 的 api() 只抛 Error(message)、不透出原始 HTTP 状态码，
      // 409（已有任务在跑）靠后端 detail 文案里的关键字兜底识别。
      if (/进行中|冲突|conflict/i.test(msg)) {
        toast("已有抓取任务进行中，请稍后再试", "error");
      } else {
        toast(msg, "error");
      }
    } finally {
      setRunStarting(false);
    }
  }

  async function handleImport() {
    setImporting(true);
    try {
      const res = await importImageCategories({ kind: "companion" });
      toast(
        `导入完成：扫描 ${res.scanned} 个栏目，新建 ${res.created}，挂载 ${res.attached}，跳过 ${res.skipped}`,
        "success",
      );
      onImported?.();
    } catch (e) {
      toast(e instanceof Error ? e.message : "从图片库导入失败", "error");
    } finally {
      setImporting(false);
    }
  }

  const summaryText = lastFinishedAt
    ? `完成于 ${lastFinishedAt}${summarizeRun(lastSummary) ? ` · ${summarizeRun(lastSummary)}` : ""}`
    : "尚未运行";

  return (
    <Modal
      title="陪衬游戏定时抓取 · 配置"
      onClose={onClose}
      width={540}
      footer={
        <>
          <button className="secondaryButton" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primaryButton" type="button" onClick={() => void handleSave()} disabled={saving}>
            {saving ? "保存中…" : "保存"}
          </button>
        </>
      }
    >
      <div className="glIngestModalBody">
        <div className="glIngestModalToggleRow">
          <span className="aiFormLabel">启用定时抓取</span>
          <button
            type="button"
            className={`glIngestToggle${form.enabled ? " glIngestToggleOn" : ""}`}
            role="switch"
            aria-checked={form.enabled}
            aria-label="启用定时抓取"
            onClick={() => setField("enabled", !form.enabled)}
          >
            <span className="glIngestToggleKnob" />
          </button>
        </div>

        <div className="glIngestModalGrid">
          <label className="aiFormGroup">
            <span className="aiFormLabel">窗口开始（HH:MM）</span>
            <input
              className="aiSearchInput"
              value={form.window_start}
              placeholder="03:30"
              onChange={(e) => setField("window_start", e.target.value)}
            />
          </label>
          <label className="aiFormGroup">
            <span className="aiFormLabel">窗口结束（HH:MM）</span>
            <input
              className="aiSearchInput"
              value={form.window_end}
              placeholder="06:00"
              onChange={(e) => setField("window_end", e.target.value)}
            />
          </label>
          <label className="aiFormGroup">
            <span className="aiFormLabel">每批数量</span>
            <input
              className="aiSearchInput"
              type="number"
              min={1}
              value={form.batch_size}
              onChange={(e) => setField("batch_size", Number(e.target.value))}
            />
          </label>
          <label className="aiFormGroup">
            <span className="aiFormLabel">单游戏截图上限</span>
            <input
              className="aiSearchInput"
              type="number"
              min={1}
              value={form.max_shots}
              onChange={(e) => setField("max_shots", Number(e.target.value))}
            />
          </label>
          <label className="aiFormGroup">
            <span className="aiFormLabel">最小间隔（秒）</span>
            <input
              className="aiSearchInput"
              type="number"
              min={0}
              value={form.min_gap_seconds}
              onChange={(e) => setField("min_gap_seconds", Number(e.target.value))}
            />
          </label>
          <label className="aiFormGroup">
            <span className="aiFormLabel">最大间隔（秒）</span>
            <input
              className="aiSearchInput"
              type="number"
              min={0}
              value={form.max_gap_seconds}
              onChange={(e) => setField("max_gap_seconds", Number(e.target.value))}
            />
          </label>
        </div>

        <label className="aiFormGroup">
          <span className="aiFormLabel">来源顺序（source_order）</span>
          <input
            className="aiSearchInput"
            value={form.source_order}
            placeholder="如 taptap,baidu"
            onChange={(e) => setField("source_order", e.target.value)}
          />
        </label>

        <div className="glIngestModalRunRow">
          <button
            type="button"
            className="secondaryButton"
            disabled={runStarting || running}
            onClick={() => void handleRunNow()}
          >
            {running ? "抓取中…" : runStarting ? "触发中…" : "立即抓取一批"}
          </button>
          <button type="button" className="secondaryButton" disabled={importing} onClick={() => void handleImport()}>
            {importing ? "导入中…" : "从图片库导入"}
          </button>
        </div>

        <div className="glIngestModalSummary">
          <span className="aiFormLabel">上次运行</span>
          <p className="glIngestModalSummaryText">{summaryText}</p>
        </div>
      </div>
    </Modal>
  );
}
