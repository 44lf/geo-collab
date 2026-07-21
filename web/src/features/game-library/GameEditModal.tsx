import { useState } from "react";
import { updateGame } from "../../api/game-library";
import type { GameDetail } from "../../types";
import { Modal } from "../../components/Modal";
import { useToast } from "../../components/Toast";

type Props = {
  game: GameDetail;
  onClose: () => void;
  onSaved: (updated: GameDetail) => void;
};

export function GameEditModal({ game, onClose, onSaved }: Props) {
  const { toast } = useToast();
  const [name, setName] = useState(game.name);
  const [score, setScore] = useState(game.score != null ? String(game.score) : "");
  const [description, setDescription] = useState(game.description ?? "");
  const [tagsText, setTagsText] = useState(game.tags.join(", "));
  const [saving, setSaving] = useState(false);

  async function handleSave() {
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
      const updated = await updateGame(game.game_id, {
        name: trimmedName,
        score: parsedScore,
        description: description.trim() || null,
        tags,
      });
      onSaved(updated);
      toast("已保存游戏信息", "success");
      onClose();
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存游戏信息失败", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title="编辑游戏信息"
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
            onClick={() => void handleSave()}
            disabled={saving}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </>
      }
    >
      <div className="glIngestModalBody">
        <label className="aiFormGroup">
          <span className="aiFormLabel">游戏名</span>
          <input className="aiSearchInput" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="aiFormGroup">
          <span className="aiFormLabel">评分</span>
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
          <span className="aiFormLabel">标签（逗号分隔）</span>
          <input
            className="aiSearchInput"
            value={tagsText}
            placeholder="如 二次元, 卡牌"
            onChange={(e) => setTagsText(e.target.value)}
          />
        </label>
        <label className="aiFormGroup">
          <span className="aiFormLabel">简介</span>
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
