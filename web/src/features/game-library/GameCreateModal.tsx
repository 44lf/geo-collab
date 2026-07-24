import { useState } from "react";
import { createGame } from "../../api/game-library";
import type { GameDetail } from "../../types";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";

type Props = {
  defaultKind: "main" | "companion";
  onClose: () => void;
  onCreated: (created: GameDetail) => void;
};

export function GameCreateModal({ defaultKind, onClose, onCreated }: Props) {
  const { toast } = useToast();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"main" | "companion">(defaultKind);
  const [score, setScore] = useState("");
  const [description, setDescription] = useState("");
  const [tagsText, setTagsText] = useState("");
  const [saving, setSaving] = useState(false);

  async function handleCreate() {
    const trimmedName = name.trim();
    if (!trimmedName) {
      toast("游戏名不能为空", "error");
      return;
    }
    const parsedScore = score.trim() === "" ? null : Number(score);
    if (parsedScore != null && Number.isNaN(parsedScore)) {
      toast("评分需为数字", "error");
      return;
    }
    const tags = tagsText
      .split(/[,，]/)
      .map((t) => t.trim())
      .filter(Boolean);

    setSaving(true);
    try {
      const created = await createGame({
        name: trimmedName,
        kind,
        score: parsedScore,
        description: description.trim() || null,
        tags,
      });
      onCreated(created);
      toast("已新建游戏", "success");
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : "新建游戏失败", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title="新建游戏"
      onClose={onClose}
      width={480}
      footer={
        <>
          <button className="secondaryButton" type="button" onClick={onClose}>
            取消
          </button>
          <button
            className="primaryButton"
            type="button"
            onClick={() => void handleCreate()}
            disabled={saving}
          >
            {saving ? "创建中…" : "创建"}
          </button>
        </>
      }
    >
      <div className="glIngestModalBody">
        <label className="aiFormGroup">
          <span className="aiFormLabel">游戏名</span>
          <input
            className="aiSearchInput"
            value={name}
            placeholder="如 原神"
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="aiFormGroup">
          <span className="aiFormLabel">归属</span>
          <select
            className="aiSearchInput"
            value={kind}
            onChange={(e) => setKind(e.target.value as "main" | "companion")}
          >
            <option value="main">主推游戏</option>
            <option value="companion">陪衬游戏</option>
          </select>
        </label>
        <label className="aiFormGroup">
          <span className="aiFormLabel">评分（选填）</span>
          <input
            className="aiSearchInput"
            type="number"
            min={0}
            max={10}
            step={0.1}
            value={score}
            placeholder="留空表示无评分"
            onChange={(e) => setScore(e.target.value)}
          />
        </label>
        <label className="aiFormGroup">
          <span className="aiFormLabel">标签（逗号分隔，选填）</span>
          <input
            className="aiSearchInput"
            value={tagsText}
            placeholder="如 二次元, 卡牌"
            onChange={(e) => setTagsText(e.target.value)}
          />
        </label>
        <label className="aiFormGroup">
          <span className="aiFormLabel">简介（选填）</span>
          <textarea
            className="aiTextarea"
            rows={4}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </label>
      </div>
    </Modal>
  );
}
