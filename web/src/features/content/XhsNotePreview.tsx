import { EditorContent, useEditor } from "@tiptap/react";
import { useMemo, useState } from "react";
import { updateArticle } from "../../api/articles";
import type { Article } from "../../types";
import { useToast } from "../../components/Toast";
import { buildReadonlyExtensions } from "./readonlyExtensions";

type XhsDoc = { type: "doc"; content: Record<string, unknown>[] };

/** 拆 content_json → 图节点（轮播，按序，保留完整原始节点）+ 文本节点（可编辑） */
export function splitXhsDoc(doc: unknown): { images: Record<string, unknown>[]; textDoc: XhsDoc } {
  const content = (doc as XhsDoc | undefined)?.content;
  const nodes = Array.isArray(content) ? content : [];
  const images: Record<string, unknown>[] = [];
  const textNodes: Record<string, unknown>[] = [];
  for (const n of nodes) {
    if ((n as { type?: string }).type === "image") {
      const attrs = (n as { attrs?: { src?: string } }).attrs ?? {};
      if (attrs.src) images.push(n);
    } else {
      textNodes.push(n);
    }
  }
  return { images, textDoc: { type: "doc", content: textNodes } };
}

export function XhsNotePreview({ article, onSaved }: { article: Article; onSaved?: () => void }) {
  const { toast } = useToast();
  const { images, textDoc } = useMemo(() => splitXhsDoc(article.content_json), [article.content_json]);
  const [index, setIndex] = useState(0);
  const [title, setTitle] = useState(article.title);
  const [saving, setSaving] = useState(false);
  const canEdit = article.can_edit !== false;

  const editor = useEditor(
    { extensions: buildReadonlyExtensions(), content: textDoc, editable: canEdit },
    [article.id],
  );

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
      await updateArticle(article.id, { title, content_json, content_html, plain_text });
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
    <div className="xhsNotePreview">
      {/* 轮播（图片锁定） */}
      <div className="xhsCarousel" style={{ position: "relative", textAlign: "center" }}>
        {n > 0 ? (
          <img
            src={(images[index] as { attrs?: { src?: string } }).attrs?.src}
            alt={`图 ${index + 1}`}
            style={{ maxWidth: "100%", maxHeight: 520, borderRadius: "var(--r)", objectFit: "contain" }}
          />
        ) : (
          <div className="aiEmptyText" style={{ padding: 48 }}>无图片</div>
        )}
        {n > 1 && (
          <>
            <button
              className="iconButton"
              type="button"
              style={{ position: "absolute", left: 8, top: "50%" }}
              onClick={() => setIndex((i) => (i - 1 + n) % n)}
            >
              ‹
            </button>
            <button
              className="iconButton"
              type="button"
              style={{ position: "absolute", right: 8, top: "50%" }}
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

      {/* 文案（可编辑） */}
      <div style={{ marginTop: 16 }}>
        <input
          value={title}
          disabled={!canEdit}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="标题"
          style={{ width: "100%", fontSize: 18, fontWeight: 600, marginBottom: 8 }}
        />
        <div className="editorWrap paper-scope">
          <EditorContent editor={editor} />
        </div>
        {canEdit && (
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
            <button className="primaryButton" type="button" disabled={saving} onClick={() => void save()}>
              {saving ? "保存中…" : "保存文案"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
