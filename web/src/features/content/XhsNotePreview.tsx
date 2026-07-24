import { EditorContent, useEditor } from "@tiptap/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { updateArticle } from "../../api/articles";
import type { Article } from "../../types";
import { useToast } from "../../components/Toast";
import { buildReadonlyExtensions } from "./readonlyExtensions";

type XhsDoc = { type: "doc"; content: Record<string, unknown>[] };

/**
 * 拆 content_json → 图节点（轮播，按序，保留完整原始节点）+ 文本节点（可编辑）。
 * 文本节点里把 heading 降级成 paragraph：小红书文案的 `#标签` 行曾被 markdown 当成
 * ATX 标题（`#词` 无空格也被 python-markdown 识别成 H1），导致标签超大——笔记文案本就
 * 不该有标题级，统一降为段落。
 */
export function splitXhsDoc(doc: unknown): { images: Record<string, unknown>[]; textDoc: XhsDoc } {
  const content = (doc as XhsDoc | undefined)?.content;
  const nodes = Array.isArray(content) ? content : [];
  const images: Record<string, unknown>[] = [];
  const textNodes: Record<string, unknown>[] = [];
  for (const n of nodes) {
    const type = (n as { type?: string }).type;
    if (type === "image") {
      const attrs = (n as { attrs?: { src?: string } }).attrs ?? {};
      if (attrs.src) images.push(n);
    } else if (type === "heading") {
      textNodes.push({ type: "paragraph", content: (n as { content?: unknown }).content ?? [] });
    } else {
      textNodes.push(n);
    }
  }
  return { images, textDoc: { type: "doc", content: textNodes } };
}

export function XhsNotePreview({
  article,
  onSaved,
  onDirtyChange,
}: {
  article: Article;
  onSaved?: () => void;
  /** 未保存改动上报给父组件（并入离开拦截）。 */
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const { toast } = useToast();
  const { images, textDoc } = useMemo(
    () => splitXhsDoc(article.content_json),
    [article.content_json],
  );
  const [index, setIndex] = useState(0);
  const [title, setTitle] = useState(article.title);
  const [baselineTitle, setBaselineTitle] = useState(article.title);
  const [copyDirty, setCopyDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const canEdit = article.can_edit !== false;
  const initialCopyRef = useRef<string>("");
  const [lightbox, setLightbox] = useState<string | null>(null);

  // 放大预览：Esc 关闭
  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setLightbox(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox]);

  const editor = useEditor(
    {
      extensions: buildReadonlyExtensions(),
      content: textDoc,
      editable: canEdit,
      onCreate: ({ editor }) => {
        initialCopyRef.current = JSON.stringify(editor.getJSON());
      },
      onUpdate: ({ editor }) => {
        setCopyDirty(JSON.stringify(editor.getJSON()) !== initialCopyRef.current);
      },
    },
    [article.id],
  );

  const dirty = copyDirty || title !== baselineTitle;

  // 上报脏态（父组件把它并进 isDirty，用于切文章/离开拦截）
  useEffect(() => {
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);
  // 卸载（切走 xhs / 换文章 remount）时清脏态，避免残留误拦普通文章
  useEffect(() => {
    return () => onDirtyChange?.(false);
  }, [onDirtyChange]);

  async function save() {
    if (!editor) return;
    setSaving(true);
    try {
      const copyJson = editor.getJSON() as XhsDoc;
      const content_json = { type: "doc", content: [...images, ...(copyJson.content ?? [])] };
      const imagesHtml = images
        .map((im) => {
          const attrs = (im as { attrs?: { src?: string; alt?: string } }).attrs ?? {};
          return `<img src="${attrs.src ?? ""}" alt="${attrs.alt ?? ""}" />`;
        })
        .join("");
      const content_html = imagesHtml + editor.getHTML();
      const plain_text = editor.getText();
      await updateArticle(article.id, {
        title,
        content_json,
        content_html,
        plain_text,
        word_count: plain_text.length,
        version: article.version, // 乐观并发：命中冲突后端回 409
      });
      // 保存成功 → 重置脏态基线
      initialCopyRef.current = JSON.stringify(copyJson);
      setBaselineTitle(title);
      setCopyDirty(false);
      toast("已保存", "success");
      onSaved?.();
    } catch (e) {
      toast(e instanceof Error ? e.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  const n = images.length;
  return (
    <div
      className="xhsNotePreview"
      style={{ display: "flex", gap: 20, alignItems: "flex-start", flexWrap: "wrap" }}
    >
      {/* 左列：轮播（图片锁定，尺寸受限，给文案让出空间） */}
      <div
        className="xhsCarousel"
        style={{ flex: "0 0 320px", maxWidth: 320, position: "relative", textAlign: "center" }}
      >
        {n > 0 ? (
          <img
            src={(images[index] as { attrs?: { src?: string } }).attrs?.src}
            alt={`图 ${index + 1}`}
            title="点击放大"
            onClick={() => {
              const s = (images[index] as { attrs?: { src?: string } }).attrs?.src;
              if (s) setLightbox(s);
            }}
            style={{
              maxWidth: "100%",
              maxHeight: 420,
              borderRadius: "var(--r)",
              objectFit: "contain",
              cursor: "zoom-in",
            }}
          />
        ) : (
          <div className="aiEmptyText" style={{ padding: 48 }}>
            无图片
          </div>
        )}
        {n > 1 && (
          <>
            <button
              className="iconButton"
              type="button"
              style={{ position: "absolute", left: 0, top: "45%" }}
              onClick={() => setIndex((i) => (i - 1 + n) % n)}
            >
              ‹
            </button>
            <button
              className="iconButton"
              type="button"
              style={{ position: "absolute", right: 0, top: "45%" }}
              onClick={() => setIndex((i) => (i + 1) % n)}
            >
              ›
            </button>
            <div style={{ display: "flex", gap: 6, justifyContent: "center", marginTop: 8 }}>
              {images.map((_, i) => (
                <span
                  key={i}
                  onClick={() => setIndex(i)}
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    cursor: "pointer",
                    background: i === index ? "var(--text, #333)" : "var(--border, #ccc)",
                  }}
                />
              ))}
            </div>
          </>
        )}
      </div>

      {/* 右列：文案（可编辑，占主要空间） */}
      <div style={{ flex: "1 1 380px", minWidth: 320, display: "flex", flexDirection: "column" }}>
        <input
          value={title}
          disabled={!canEdit}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="标题"
          style={{ width: "100%", fontSize: 18, fontWeight: 600, marginBottom: 8 }}
        />
        <div
          className="editorWrap paper-scope"
          style={{ flex: 1, minHeight: 320, overflowY: "auto" }}
        >
          <EditorContent editor={editor} />
        </div>
        {canEdit && (
          <div
            style={{
              display: "flex",
              justifyContent: "flex-end",
              alignItems: "center",
              gap: 10,
              marginTop: 8,
            }}
          >
            {dirty && <span style={{ fontSize: 12, color: "var(--red, #e67e22)" }}>未保存</span>}
            <button
              className="primaryButton"
              type="button"
              disabled={saving || !dirty}
              onClick={() => void save()}
            >
              {saving ? "保存中…" : "保存文案"}
            </button>
          </div>
        )}
      </div>

      {/* 放大预览：portal 到 body，避开被 transform/contain 限制的祖先，铺满视口。点背景/×/Esc 关闭 */}
      {lightbox &&
        createPortal(
          <div
            onClick={() => setLightbox(null)}
            style={{
              position: "fixed",
              inset: 0,
              zIndex: 1000,
              background: "rgba(0,0,0,0.82)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              cursor: "zoom-out",
            }}
          >
            <button
              type="button"
              aria-label="关闭"
              onClick={(e) => {
                e.stopPropagation();
                setLightbox(null);
              }}
              style={{
                position: "absolute",
                top: 20,
                right: 28,
                fontSize: 30,
                lineHeight: 1,
                color: "#fff",
                background: "transparent",
                border: "none",
                cursor: "pointer",
              }}
            >
              ×
            </button>
            <img
              src={lightbox}
              alt="放大预览"
              onClick={(e) => e.stopPropagation()}
              style={{
                maxWidth: "92vw",
                maxHeight: "92vh",
                objectFit: "contain",
                borderRadius: 8,
                cursor: "default",
              }}
            />
          </div>,
          document.body,
        )}
    </div>
  );
}
