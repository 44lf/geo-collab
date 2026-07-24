import { useState } from "react";
import {
  importImageCategories,
  patchIngestConfig,
  startDiscoveryRun,
  startIngestRun,
} from "../../api/game-library";
import type { GameIngestConfig } from "../../types";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";

type Props = {
  config: GameIngestConfig;
  onClose: () => void;
  onSaved: (cfg: GameIngestConfig) => void;
  onImported?: () => void;
};

type Seg = "patrol" | "discovery";

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
  const [seg, setSeg] = useState<Seg>("patrol");
  const [form, setForm] = useState({
    // 补全（按名巡检）
    enabled: config.enabled,
    window_start: config.window_start,
    window_end: config.window_end,
    batch_size: config.batch_size,
    min_gap_seconds: config.min_gap_seconds,
    max_gap_seconds: config.max_gap_seconds,
    source_order: config.source_order,
    max_shots: config.max_shots,
    cull_enabled: config.cull_enabled,
    cull_after_misses: config.cull_after_misses,
    patrol_include_main: config.patrol_include_main,
    // 扩库（榜单发现）
    d_enabled: config.discovery.enabled,
    d_window_start: config.discovery.window_start,
    d_window_end: config.discovery.window_end,
    d_detail_limit: config.discovery.detail_limit,
    d_max_shots: config.discovery.max_shots,
    d_min_gap_seconds: config.discovery.min_gap_seconds,
    d_max_gap_seconds: config.discovery.max_gap_seconds,
    d_seed_paths: (config.discovery.seed_paths ?? []).join("\n"),
  });
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  // 补全 立即抓
  const [running, setRunning] = useState(config.running);
  const [runStarting, setRunStarting] = useState(false);
  const [lastFinishedAt, setLastFinishedAt] = useState(config.last_run_finished_at);
  const [lastSummary, setLastSummary] = useState(config.last_run_summary);
  // 扩库 立即跑
  const [dRunning, setDRunning] = useState(config.discovery.running);
  const [dRunStarting, setDRunStarting] = useState(false);
  const [dLastFinishedAt, setDLastFinishedAt] = useState(config.discovery.last_run_finished_at);
  const [dLastSummary, setDLastSummary] = useState(config.discovery.last_run_summary);

  function setField<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function handleSave() {
    setSaving(true);
    try {
      const seeds = form.d_seed_paths
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);
      const next = await patchIngestConfig({
        enabled: form.enabled,
        window_start: form.window_start.trim(),
        window_end: form.window_end.trim(),
        batch_size: form.batch_size,
        min_gap_seconds: form.min_gap_seconds,
        max_gap_seconds: form.max_gap_seconds,
        source_order: form.source_order.trim(),
        max_shots: form.max_shots,
        cull_enabled: form.cull_enabled,
        cull_after_misses: form.cull_after_misses,
        patrol_include_main: form.patrol_include_main,
        discovery: {
          enabled: form.d_enabled,
          window_start: form.d_window_start.trim(),
          window_end: form.d_window_end.trim(),
          detail_limit: form.d_detail_limit,
          max_shots: form.d_max_shots,
          min_gap_seconds: form.d_min_gap_seconds,
          max_gap_seconds: form.d_max_gap_seconds,
          seed_paths: seeds.length ? seeds : null,
        },
      });
      onSaved(next);
      toast("已保存自动采集配置", "success");
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存配置失败", "error");
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
      toast(
        res.started ? "已开始补全一批" : "补全任务已在进行中，请稍后再试",
        res.started ? "success" : "error",
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : "触发补全失败";
      if (/进行中|冲突|conflict/i.test(msg)) {
        toast("已有补全任务进行中，请稍后再试", "error");
      } else {
        toast(msg, "error");
      }
    } finally {
      setRunStarting(false);
    }
  }

  async function handleDiscoveryRun() {
    if (dRunStarting || dRunning) return;
    setDRunStarting(true);
    try {
      const res = await startDiscoveryRun();
      setDRunning(res.status.discovery.running);
      setDLastFinishedAt(res.status.discovery.last_run_finished_at);
      setDLastSummary(res.status.discovery.last_run_summary);
      toast(
        res.started ? "已开始扩库一批" : "扩库任务已在进行中，请稍后再试",
        res.started ? "success" : "error",
      );
    } catch (e) {
      const msg = e instanceof Error ? e.message : "触发扩库失败";
      if (/进行中|冲突|conflict/i.test(msg)) {
        toast("已有扩库任务进行中，请稍后再试", "error");
      } else {
        toast(msg, "error");
      }
    } finally {
      setDRunStarting(false);
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

  const patrolSummary = lastFinishedAt
    ? `完成于 ${lastFinishedAt}${summarizeRun(lastSummary) ? ` · ${summarizeRun(lastSummary)}` : ""}`
    : "尚未运行";
  const discoverySummary = dLastFinishedAt
    ? `完成于 ${dLastFinishedAt}${summarizeRun(dLastSummary) ? ` · ${summarizeRun(dLastSummary)}` : ""}`
    : "尚未运行";

  return (
    <Modal
      title="自动采集 · 配置"
      onClose={onClose}
      width={560}
      footer={
        <>
          <button className="secondaryButton" type="button" onClick={onClose}>
            取消
          </button>
          <button
            className="primaryButton"
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </>
      }
    >
      <div className="glIngestModalBody">
        <div className="glGroupSwitch">
          <button
            type="button"
            className={`glSegBtn${seg === "patrol" ? " active" : ""}`}
            onClick={() => setSeg("patrol")}
          >
            补全 · 按名巡检
          </button>
          <button
            type="button"
            className={`glSegBtn${seg === "discovery" ? " active" : ""}`}
            onClick={() => setSeg("discovery")}
          >
            扩库 · 榜单发现
          </button>
        </div>

        {seg === "patrol" ? (
          <>
            <p className="glIngestFlyoutSub">
              按你库里现有游戏名逐个去各源精确搜、补空字段（不新增游戏）。
            </p>
            <div className="glIngestModalToggleRow">
              <span className="aiFormLabel">启用定时补全</span>
              <button
                type="button"
                className={`glIngestToggle${form.enabled ? " glIngestToggleOn" : ""}`}
                role="switch"
                aria-checked={form.enabled}
                aria-label="启用定时补全"
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
                  placeholder="03:00"
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
              <span className="aiFormLabel">来源顺序（source_order，可填 baidu,ninegame）</span>
              <input
                className="aiSearchInput"
                value={form.source_order}
                placeholder="baidu,ninegame"
                onChange={(e) => setField("source_order", e.target.value)}
              />
            </label>

            <div className="glIngestModalToggleRow">
              <span className="aiFormLabel">同时巡检主推游戏（默认只巡检陪衬）</span>
              <button
                type="button"
                className={`glIngestToggle${form.patrol_include_main ? " glIngestToggleOn" : ""}`}
                role="switch"
                aria-checked={form.patrol_include_main}
                aria-label="同时巡检主推游戏"
                onClick={() => setField("patrol_include_main", !form.patrol_include_main)}
              >
                <span className="glIngestToggleKnob" />
              </button>
            </div>

            <div className="glIngestModalToggleRow">
              <span className="aiFormLabel">无证据自动软删（连续搜不到）</span>
              <button
                type="button"
                className={`glIngestToggle${form.cull_enabled ? " glIngestToggleOn" : ""}`}
                role="switch"
                aria-checked={form.cull_enabled}
                aria-label="启用无证据自动软删"
                onClick={() => setField("cull_enabled", !form.cull_enabled)}
              >
                <span className="glIngestToggleKnob" />
              </button>
            </div>
            <label className="aiFormGroup">
              <span className="aiFormLabel">连续几轮全源未命中才软删</span>
              <input
                className="aiSearchInput"
                type="number"
                min={1}
                value={form.cull_after_misses}
                onChange={(e) => setField("cull_after_misses", Number(e.target.value))}
              />
            </label>

            <div className="glIngestModalRunRow">
              <button
                type="button"
                className="secondaryButton"
                disabled={runStarting || running}
                onClick={() => void handleRunNow()}
              >
                {running ? "补全中…" : runStarting ? "触发中…" : "立即补全一批"}
              </button>
              <button
                type="button"
                className="secondaryButton"
                disabled={importing}
                onClick={() => void handleImport()}
              >
                {importing ? "导入中…" : "从图片库导入"}
              </button>
            </div>

            <div className="glIngestModalSummary">
              <span className="aiFormLabel">上次补全</span>
              <p className="glIngestModalSummaryText">{patrolSummary}</p>
            </div>
          </>
        ) : (
          <>
            <p className="glIngestFlyoutSub">
              爬应用宝榜单/分类/标签页发现新游戏并落库（自动建陪衬桶、补全字段含截图与精选评论）。
            </p>
            <div className="glIngestModalToggleRow">
              <span className="aiFormLabel">启用定时扩库</span>
              <button
                type="button"
                className={`glIngestToggle${form.d_enabled ? " glIngestToggleOn" : ""}`}
                role="switch"
                aria-checked={form.d_enabled}
                aria-label="启用定时扩库"
                onClick={() => setField("d_enabled", !form.d_enabled)}
              >
                <span className="glIngestToggleKnob" />
              </button>
            </div>

            <div className="glIngestModalGrid">
              <label className="aiFormGroup">
                <span className="aiFormLabel">窗口开始（HH:MM）</span>
                <input
                  className="aiSearchInput"
                  value={form.d_window_start}
                  placeholder="04:00"
                  onChange={(e) => setField("d_window_start", e.target.value)}
                />
              </label>
              <label className="aiFormGroup">
                <span className="aiFormLabel">窗口结束（HH:MM）</span>
                <input
                  className="aiSearchInput"
                  value={form.d_window_end}
                  placeholder="06:00"
                  onChange={(e) => setField("d_window_end", e.target.value)}
                />
              </label>
              <label className="aiFormGroup">
                <span className="aiFormLabel">每轮补详情数</span>
                <input
                  className="aiSearchInput"
                  type="number"
                  min={1}
                  value={form.d_detail_limit}
                  onChange={(e) => setField("d_detail_limit", Number(e.target.value))}
                />
              </label>
              <label className="aiFormGroup">
                <span className="aiFormLabel">单游戏截图上限</span>
                <input
                  className="aiSearchInput"
                  type="number"
                  min={1}
                  value={form.d_max_shots}
                  onChange={(e) => setField("d_max_shots", Number(e.target.value))}
                />
              </label>
              <label className="aiFormGroup">
                <span className="aiFormLabel">最小间隔（秒）</span>
                <input
                  className="aiSearchInput"
                  type="number"
                  min={1}
                  value={form.d_min_gap_seconds}
                  onChange={(e) => setField("d_min_gap_seconds", Number(e.target.value))}
                />
              </label>
              <label className="aiFormGroup">
                <span className="aiFormLabel">最大间隔（秒）</span>
                <input
                  className="aiSearchInput"
                  type="number"
                  min={1}
                  value={form.d_max_gap_seconds}
                  onChange={(e) => setField("d_max_gap_seconds", Number(e.target.value))}
                />
              </label>
            </div>

            <label className="aiFormGroup">
              <span className="aiFormLabel">种子榜单路径（每行一个，留空=用内置 16 个）</span>
              <textarea
                className="aiSearchInput"
                rows={4}
                value={form.d_seed_paths}
                placeholder={"/hot-game-list\n/new-game-list\n/tag/roguelike"}
                onChange={(e) => setField("d_seed_paths", e.target.value)}
              />
            </label>

            <div className="glIngestModalRunRow">
              <button
                type="button"
                className="secondaryButton"
                disabled={dRunStarting || dRunning}
                onClick={() => void handleDiscoveryRun()}
              >
                {dRunning ? "扩库中…" : dRunStarting ? "触发中…" : "立即扩库一批"}
              </button>
            </div>

            <div className="glIngestModalSummary">
              <span className="aiFormLabel">上次扩库</span>
              <p className="glIngestModalSummaryText">{discoverySummary}</p>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
