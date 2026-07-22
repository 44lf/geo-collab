import { useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, ImageIcon, Trash2, Upload, X } from "lucide-react";
import { deleteImage, listImages, uploadImage } from "../../api/image-library";
import type { StockImage } from "../../types";
import { useToast } from "../../components/Toast";

const PAGE_SIZE = 12;

// 生成页码列表；超过 7 页时用省略号折叠。与图片库 GridPagination 分页逻辑一致。
function pageList(page: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const out: (number | "…")[] = [1];
  const left = Math.max(2, page - 1);
  const right = Math.min(total - 1, page + 1);
  if (left > 2) out.push("…");
  for (let p = left; p <= right; p++) out.push(p);
  if (right < total - 1) out.push("…");
  out.push(total);
  return out;
}

export function GameMaterialPanel({
  categoryId,
  screenshotUrlCount,
}: {
  categoryId: number | null;
  screenshotUrlCount: number;
}) {
  const { toast } = useToast();
  const [images, setImages] = useState<StockImage[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [lightbox, setLightbox] = useState<StockImage | null>(null);
  const [currentPage, setCurrentPage] = useState(1);
  const fileRef = useRef<HTMLInputElement>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    setCurrentPage(1);
    if (categoryId == null) {
      setImages([]);
      return;
    }
    const seq = ++seqRef.current;
    setLoading(true);
    listImages({ category_id: categoryId })
      .then((imgs) => {
        if (seq === seqRef.current) setImages(imgs);
      })
      .catch(() => {
        if (seq === seqRef.current) toast("加载素材失败", "error");
      })
      .finally(() => {
        if (seq === seqRef.current) setLoading(false);
      });
  }, [categoryId]); // eslint-disable-line react-hooks/exhaustive-deps

  // 删除等导致总页数减少时，把 currentPage 钳回合法范围，避免停在空页。
  useEffect(() => {
    const total = Math.max(1, Math.ceil(images.length / PAGE_SIZE));
    if (currentPage > total) setCurrentPage(total);
  }, [images, currentPage]);

  async function onPick(files: FileList | null) {
    if (!files || files.length === 0 || categoryId == null) return;
    setUploading(true);
    let ok = 0;
    for (let i = 0; i < files.length; i++) {
      try {
        const img = await uploadImage({ category_id: categoryId, file: files[i] });
        setImages((prev) => [img, ...prev]);
        ok++;
      } catch {
        toast(`第 ${i + 1} 张上传失败`, "error");
      }
    }
    setUploading(false);
    if (fileRef.current) fileRef.current.value = "";
    if (ok > 0) setCurrentPage(1);
    toast(`上传完成：${ok}/${files.length} 张`, ok === files.length ? "success" : "error");
  }

  async function onDelete(img: StockImage) {
    if (!window.confirm(`删除素材「${img.filename}」？`)) return;
    try {
      await deleteImage(img.id);
      setImages((prev) => prev.filter((i) => i.id !== img.id));
      toast("已删除", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : "删除失败", "error");
    }
  }

  const count = categoryId == null ? screenshotUrlCount : images.length;
  const totalPages = Math.max(1, Math.ceil(images.length / PAGE_SIZE));
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const pageImages = images.slice(pageStart, pageStart + PAGE_SIZE);

  return (
    <div className="glShots">
      <div className="glShotsHead">
        <div className="glShotsTitle">
          <span>游戏素材</span>
          <span className="glShotsBadge">{count}</span>
        </div>
        <button
          type="button"
          className="glUploadBtn"
          disabled={categoryId == null || uploading}
          onClick={() => fileRef.current?.click()}
        >
          <Upload size={15} /> {uploading ? "上传中…" : "上传图片"}
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp,image/gif"
          style={{ display: "none" }}
          onChange={(e) => void onPick(e.target.files)}
        />
      </div>

      {categoryId == null ? (
        <div className="glShotsEmpty">
          <ImageIcon size={26} strokeWidth={1.3} />
          <span>该游戏暂无素材桶（stock_category_id 为空）</span>
        </div>
      ) : loading ? (
        <div className="glShotsEmpty"><span>加载素材中…</span></div>
      ) : images.length === 0 ? (
        <div className="glShotsEmpty">
          <ImageIcon size={26} strokeWidth={1.3} />
          <span>暂无截图素材，点「上传图片」添加</span>
        </div>
      ) : (
        <>
          <div className="glShotsGrid">
            {pageImages.map((img) => (
              <div key={img.id} className="glShotCard">
                <div className="glShotThumb" onClick={() => setLightbox(img)}>
                  <img src={img.url} alt={img.filename} loading="lazy" />
                  <button
                    type="button"
                    className="glShotDel"
                    title="删除"
                    onClick={(e) => { e.stopPropagation(); void onDelete(img); }}
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
                <span className="glShotName mono" title={img.filename}>{img.filename}</span>
              </div>
            ))}
          </div>
          {totalPages > 1 && (
            <div className="glShotsPager">
              <button
                type="button"
                className="glShotsPagerBtn"
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage((p) => p - 1)}
                aria-label="上一页"
              >
                <ChevronLeft size={16} />
              </button>
              {pageList(currentPage, totalPages).map((p, i) =>
                p === "…" ? (
                  <span key={`gap-${i}`} className="glShotsPagerGap">…</span>
                ) : (
                  <button
                    key={p}
                    type="button"
                    className={`glShotsPagerBtn${p === currentPage ? " active" : ""}`}
                    onClick={() => setCurrentPage(p)}
                  >
                    {p}
                  </button>
                ),
              )}
              <button
                type="button"
                className="glShotsPagerBtn"
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage((p) => p + 1)}
                aria-label="下一页"
              >
                <ChevronRight size={16} />
              </button>
              <span className="glShotsPagerInfo">共 {images.length} 张 · 第 {currentPage}/{totalPages} 页</span>
            </div>
          )}
        </>
      )}

      {lightbox && (
        <div className="glLightbox" onClick={() => setLightbox(null)}>
          <button type="button" className="glLightboxClose" onClick={() => setLightbox(null)}>
            <X size={20} />
          </button>
          <img className="glLightboxImg" src={lightbox.url} alt={lightbox.filename} onClick={(e) => e.stopPropagation()} />
        </div>
      )}
    </div>
  );
}
