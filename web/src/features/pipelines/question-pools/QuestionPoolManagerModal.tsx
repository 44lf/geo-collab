import { useCallback, useEffect, useState } from "react";
import { Check, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import {
  createQuestionPool,
  deleteQuestionPool,
  listQuestionPools,
  syncQuestionPool,
  updateQuestionPool,
} from "../../../api/question-pools";
import { useToast } from "../../../components/Toast";
import type { QuestionPool } from "../../../types";
import { useAuth } from "../../auth/AuthContext";

export type QuestionPoolManagerModalProps = {
  open: boolean;
  onClose: () => void;
  onChanged: () => Promise<void> | void;
};

type PoolBindingForm = {
  name: string;
  feishu_app_token: string;
  feishu_table_id: string;
};

type PoolForm = PoolBindingForm & {
  auto_sync_enabled: boolean;
};

const EMPTY_BINDING_FORM: PoolBindingForm = {
  name: "",
  feishu_app_token: "",
  feishu_table_id: "",
};

const EMPTY_FORM: PoolForm = {
  ...EMPTY_BINDING_FORM,
  auto_sync_enabled: true,
};

function formFromPool(pool: QuestionPool): PoolForm {
  return {
    name: pool.name,
    feishu_app_token: pool.feishu_app_token ?? "",
    feishu_table_id: pool.feishu_table_id ?? "",
    auto_sync_enabled: pool.auto_sync_enabled,
  };
}

export function QuestionPoolManagerModal({
  open,
  onClose,
  onChanged,
}: QuestionPoolManagerModalProps) {
  const { toast } = useToast();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [pools, setPools] = useState<QuestionPool[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createForm, setCreateForm] = useState<PoolBindingForm>(EMPTY_BINDING_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState<PoolForm>(EMPTY_FORM);
  const [syncingId, setSyncingId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setPools(await listQuestionPools());
    } catch (error) {
      toast(error instanceof Error ? error.message : "加载问题源失败", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    if (open) void reload();
  }, [open, reload]);

  async function notifyChanged() {
    await reload();
    try {
      await onChanged();
    } catch (error) {
      toast(
        error instanceof Error
          ? `问题源已更新，但入口刷新失败：${error.message}`
          : "问题源已更新，但入口刷新失败",
        "error",
      );
    }
  }

  async function handleCreate() {
    if (!createForm.name.trim()) {
      toast("请填写问题源名称", "error");
      return;
    }
    try {
      await createQuestionPool({
        name: createForm.name.trim(),
        feishu_app_token: createForm.feishu_app_token.trim() || undefined,
        feishu_table_id: createForm.feishu_table_id.trim() || undefined,
      });
      setCreateForm(EMPTY_BINDING_FORM);
      setCreating(false);
      toast("已创建问题源", "success");
      await notifyChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "创建失败", "error");
    }
  }

  async function handleSync(pool: QuestionPool) {
    setSyncingId(pool.id);
    try {
      const result = await syncQuestionPool(pool.id);
      toast(
        `同步完成：新增 ${result.added}、更新 ${result.updated}、恢复 ${result.reactivated}、失效 ${result.deactivated}`,
        "success",
      );
      await notifyChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "同步失败", "error");
    } finally {
      setSyncingId(null);
    }
  }

  function startEdit(pool: QuestionPool) {
    setEditingId(pool.id);
    setEditForm(formFromPool(pool));
  }

  function cancelEdit() {
    setEditingId(null);
    setEditForm(EMPTY_FORM);
  }

  async function handleSave(poolId: number) {
    const name = editForm.name.trim();
    if (!name) {
      toast("问题源名称不能为空", "error");
      return;
    }
    setBusyId(poolId);
    try {
      await updateQuestionPool(poolId, {
        name,
        feishu_app_token: editForm.feishu_app_token.trim(),
        feishu_table_id: editForm.feishu_table_id.trim(),
        auto_sync_enabled: editForm.auto_sync_enabled,
      });
      cancelEdit();
      toast("问题源设置已保存", "success");
      await notifyChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "保存失败", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleAutoSync(pool: QuestionPool) {
    setBusyId(pool.id);
    try {
      await updateQuestionPool(pool.id, {
        name: pool.name,
        feishu_app_token: pool.feishu_app_token ?? "",
        feishu_table_id: pool.feishu_table_id ?? "",
        auto_sync_enabled: !pool.auto_sync_enabled,
      });
      toast(`自动同步已${pool.auto_sync_enabled ? "关闭" : "开启"}`, "success");
      await notifyChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "更新自动同步失败", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function handleDelete(pool: QuestionPool) {
    if (!window.confirm(`确定删除问题源「${pool.name}」？删除后所有人都将看不到它。`)) return;
    setBusyId(pool.id);
    try {
      await deleteQuestionPool(pool.id);
      toast("已删除问题源", "success");
      await notifyChanged();
    } catch (error) {
      toast(error instanceof Error ? error.message : "删除失败", "error");
    } finally {
      setBusyId(null);
    }
  }

  if (!open) return null;

  return (
    <div className="modalBackdrop" role="dialog" aria-modal="true" onClick={onClose}>
      <div className="schemePanel questionPoolPanel" onClick={(event) => event.stopPropagation()}>
        <div className="schemePanelHead">
          <div>
            <h3>问题源管理</h3>
            <p className="schemePanelHint">
              问题源是飞书多维表的本地镜像 · 全员可维护，删除仅管理员
            </p>
          </div>
          <button className="iconButton" type="button" aria-label="关闭" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="schemePanelBody">
          {loading && pools.length === 0 && <div className="schemeEmpty">加载问题源中…</div>}
          {!loading && pools.length === 0 && !creating && (
            <div className="schemeEmpty">还没有问题源，点下方「新建问题源」开始</div>
          )}

          {pools.map((pool) => {
            const editing = editingId === pool.id;
            return (
              <div className="schemeCard questionPoolCard" key={pool.id}>
                {editing ? (
                  <div className="questionPoolEdit">
                    <label className="agentField">
                      <span className="agentFieldLabel">名称</span>
                      <input
                        autoFocus
                        value={editForm.name}
                        onChange={(event) => setEditForm({ ...editForm, name: event.target.value })}
                      />
                    </label>
                    <div className="questionPoolBindingFields">
                      <label className="agentField">
                        <span className="agentFieldLabel">飞书 app_token</span>
                        <input
                          value={editForm.feishu_app_token}
                          onChange={(event) =>
                            setEditForm({
                              ...editForm,
                              feishu_app_token: event.target.value,
                            })
                          }
                        />
                      </label>
                      <label className="agentField">
                        <span className="agentFieldLabel">飞书 table_id</span>
                        <input
                          value={editForm.feishu_table_id}
                          onChange={(event) =>
                            setEditForm({
                              ...editForm,
                              feishu_table_id: event.target.value,
                            })
                          }
                        />
                      </label>
                    </div>
                    <div className="questionPoolEditActions">
                      <label className="agentToggle">
                        <input
                          type="checkbox"
                          checked={editForm.auto_sync_enabled}
                          onChange={(event) =>
                            setEditForm({
                              ...editForm,
                              auto_sync_enabled: event.target.checked,
                            })
                          }
                        />
                        自动同步
                      </label>
                      <button
                        className="secondaryButton"
                        type="button"
                        disabled={busyId === pool.id}
                        onClick={cancelEdit}
                      >
                        <X size={14} />
                        取消
                      </button>
                      <button
                        className="primaryButton"
                        type="button"
                        disabled={busyId === pool.id}
                        onClick={() => void handleSave(pool.id)}
                      >
                        <Check size={14} />
                        保存
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="schemeCardInfo">
                      <span className="schemeCardName">{pool.name}</span>
                      <span className="questionPoolMeta">
                        {pool.last_synced_at
                          ? `上次同步 ${new Date(pool.last_synced_at).toLocaleString()}`
                          : "尚未同步"}
                        {` · 待处理 ${pool.pending_count}`}
                      </span>
                      <span className="questionPoolBinding">
                        {pool.feishu_app_token && pool.feishu_table_id
                          ? `已绑定 ${pool.feishu_app_token} / ${pool.feishu_table_id}`
                          : "未完整绑定飞书多维表"}
                      </span>
                    </div>
                    <div className="questionPoolCardActions">
                      <label className="questionPoolAutoSync">
                        <span>自动同步</span>
                        <button
                          type="button"
                          role="switch"
                          aria-checked={pool.auto_sync_enabled}
                          aria-label={`${pool.name}自动同步`}
                          className={`schemeToggle ${pool.auto_sync_enabled ? "on" : "off"}`}
                          disabled={busyId === pool.id}
                          onClick={() => void handleAutoSync(pool)}
                        >
                          <span className="knob" />
                        </button>
                      </label>
                      <button
                        className="secondaryButton"
                        type="button"
                        disabled={
                          syncingId === pool.id || !pool.feishu_app_token || !pool.feishu_table_id
                        }
                        title={
                          pool.feishu_app_token && pool.feishu_table_id
                            ? "从飞书多维表同步"
                            : "请先完整绑定飞书多维表"
                        }
                        onClick={() => void handleSync(pool)}
                      >
                        <RefreshCw size={14} />
                        {syncingId === pool.id ? "同步中…" : "立即同步"}
                      </button>
                      <button
                        className="iconButton"
                        type="button"
                        title="改名或重新绑定飞书"
                        onClick={() => startEdit(pool)}
                      >
                        <Pencil size={16} />
                      </button>
                      {isAdmin && (
                        <button
                          className="iconButton"
                          type="button"
                          disabled={busyId === pool.id}
                          title="删除问题源（仅管理员）"
                          onClick={() => void handleDelete(pool)}
                        >
                          <Trash2 size={16} />
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>
            );
          })}

          {creating ? (
            <div className="schemeLineCard questionPoolCreate">
              <label className="agentField">
                <span className="agentFieldLabel">名称（必填）</span>
                <input
                  value={createForm.name}
                  onChange={(event) => setCreateForm({ ...createForm, name: event.target.value })}
                />
              </label>
              <div className="questionPoolBindingFields">
                <label className="agentField">
                  <span className="agentFieldLabel">飞书 app_token（可选）</span>
                  <input
                    value={createForm.feishu_app_token}
                    onChange={(event) =>
                      setCreateForm({
                        ...createForm,
                        feishu_app_token: event.target.value,
                      })
                    }
                  />
                </label>
                <label className="agentField">
                  <span className="agentFieldLabel">飞书 table_id（可选）</span>
                  <input
                    value={createForm.feishu_table_id}
                    onChange={(event) =>
                      setCreateForm({
                        ...createForm,
                        feishu_table_id: event.target.value,
                      })
                    }
                  />
                </label>
              </div>
              <div className="questionPoolEditActions">
                <button
                  className="secondaryButton"
                  type="button"
                  onClick={() => {
                    setCreateForm(EMPTY_BINDING_FORM);
                    setCreating(false);
                  }}
                >
                  取消
                </button>
                <button className="primaryButton" type="button" onClick={() => void handleCreate()}>
                  创建
                </button>
              </div>
            </div>
          ) : (
            <button
              className="secondaryButton"
              type="button"
              style={{ alignSelf: "flex-start" }}
              onClick={() => setCreating(true)}
            >
              <Plus size={14} />
              新建问题源
            </button>
          )}
        </div>

        <div className="schemePanelFoot">
          <button className="secondaryButton" type="button" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
