import { useEffect, useState } from "react";
import { listCategories, uploadImage } from "../../api/image-library";
import type { StockCategory } from "../../types";
import { Modal } from "../Modal";

/**
 * WPS 风格「图片保存」弹框：把编辑器里选中的图片存进图片库。
 * 三段流程：① 主推/陪衬 tab 切 kind → ② 栏目单选列表 → ③ 文件名 + 保存。
 * 取图走 fetch(imageSrc)→Blob→File，再调 uploadImage 落 MinIO。
 */
export function ImageSaveDialog({
  imageSrc,
  onClose,
  onSaved,
  onError,
}: {
  imageSrc: string; // editor.getAttributes("image").src
  onClose: () => void;
  onSaved: (msg: string) => void;
  onError?: (msg: string) => void;
}) {
  const [kind, setKind] = useState<"main" | "companion">("companion");
  const [cats, setCats] = useState<StockCategory[]>([]);
  const [categoryId, setCategoryId] = useState<number | null>(null);
  const [filename, setFilename] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setCategoryId(null);
    listCategories(kind)
      .then((data) => {
        if (!cancelled) setCats(data);
      })
      .catch(() => {
        if (!cancelled) setCats([]);
      });
    return () => {
      cancelled = true;
    };
  }, [kind]);

  async function handleSave() {
    if (categoryId == null) {
      setError("请选择栏目");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const resp = await fetch(imageSrc);
      if (!resp.ok) throw new Error("读取图片失败");
      const blob = await resp.blob();
      const type = blob.type || "image/png";
      const ext = type.split("/")[1] || "png";
      const trimmed = filename.trim();
      const base = trimmed || `image-${Date.now()}`;
      const name = base.includes(".") ? base : `${base}.${ext}`;
      const file = new File([blob], name, { type });
      await uploadImage({ category_id: categoryId, file });
      onSaved(`已保存到图库：${name}`);
      onClose();
    } catch (e) {
      // 跨源图片 fetch 可能被 CORS 挡 → 提示后由用户改用本地上传
      const msg = e instanceof Error ? e.message : "保存失败（可能是跨源图片）";
      setError(msg);
      onError?.(msg);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title="保存图片到图库"
      onClose={onClose}
      width={460}
      footer={
        <>
          <button type="button" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button
            type="button"
            className="primaryButton"
            onClick={() => void handleSave()}
            disabled={saving || categoryId == null}
          >
            {saving ? "保存中…" : "保存"}
          </button>
        </>
      }
    >
      <div className="imageSaveDialog">
        <img className="imageSavePreview" src={imageSrc} alt="待保存图片" />

        <div className="reviewTabs imageSaveTabs">
          <button
            type="button"
            className={`reviewTabBtn ${kind === "main" ? "active" : ""}`}
            onClick={() => setKind("main")}
          >
            主推游戏
          </button>
          <button
            type="button"
            className={`reviewTabBtn ${kind === "companion" ? "active" : ""}`}
            onClick={() => setKind("companion")}
          >
            陪衬游戏
          </button>
        </div>

        <div className="imageSaveCatList">
          {cats.map((cat) => (
            <label className="groupPickerOption" key={cat.id}>
              <input
                type="radio"
                name="imageSaveCategory"
                checked={categoryId === cat.id}
                onChange={() => setCategoryId(cat.id)}
              />
              <span>{cat.name}</span>
              <small>{cat.bucket_name}</small>
            </label>
          ))}
          {cats.length === 0 ? <p className="emptyText">该分类下暂无栏目</p> : null}
        </div>

        <label className="imageSaveNameRow">
          文件名
          <input
            value={filename}
            placeholder="留空则自动命名"
            onChange={(event) => setFilename(event.target.value)}
          />
        </label>

        {error ? <p className="imageSaveError">{error}</p> : null}
      </div>
    </Modal>
  );
}
