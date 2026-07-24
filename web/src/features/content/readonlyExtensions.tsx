import { useEffect, useRef, useState } from "react";
import { NodeViewWrapper, ReactNodeViewRenderer } from "@tiptap/react";
import type { NodeViewProps } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import { TextStyle } from "@tiptap/extension-text-style";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import TextAlign from "@tiptap/extension-text-align";

// 编辑器 / 只读渲染共用的 Tiptap 扩展集合。抽出来供 ContentWorkspace（可编辑）与高质量库
// 只读 reader 复用同一套定义——正文结构（含自定义图片节点、字号 TextStyle）必须一致，否则同一份
// content_json 在两处解析出的文档不同。可编辑与只读的差异由 useEditor 的 editable 开关控制，
// 不在扩展层面区分，故这套扩展对两处都适用。

const CustomTextStyle = TextStyle.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      fontSize: {
        default: null,
        parseHTML: (el: HTMLElement) => el.style.fontSize || null,
        renderHTML: (attrs: Record<string, unknown>) =>
          attrs.fontSize ? { style: `font-size: ${attrs.fontSize}` } : {},
      },
    };
  },
});

function ImageResizeView({ node, updateAttributes, selected, editor }: NodeViewProps) {
  const attrs = node.attrs as {
    src: string;
    alt: string;
    title: string;
    assetId: string | null;
    stockImageId: number | null;
    width: string;
    progress: number | null;
  };
  const imgRef = useRef<HTMLImageElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);
  const [imgError, setImgError] = useState(false);

  const isPending = typeof attrs.assetId === "string" && attrs.assetId.startsWith("pending-");

  useEffect(() => {
    setImgError(false);
  }, [attrs.src]);

  useEffect(() => {
    return () => {
      cleanupRef.current?.();
    };
  }, []);

  function startResize(e: React.MouseEvent) {
    e.preventDefault();
    const startX = e.clientX;
    const startWidth = imgRef.current?.offsetWidth ?? 300;
    const containerWidth = imgRef.current?.parentElement?.offsetWidth || 600;

    function onMove(ev: MouseEvent) {
      const pct = Math.min(
        100,
        Math.max(10, Math.round(((startWidth + ev.clientX - startX) / containerWidth) * 100)),
      );
      updateAttributes({ width: `${pct}%` });
    }
    function onUp() {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      cleanupRef.current = null;
    }
    cleanupRef.current = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }

  return (
    <NodeViewWrapper style={{ display: "block", position: "relative", width: attrs.width ?? "100%" }}>
      {isPending && imgError ? (
        <div className="imgUploadingPlaceholder">
          <span>{attrs.progress != null ? `上传中 ${attrs.progress}%` : "上传中…"}</span>
          {attrs.progress != null && (
            <div className="imgUploadProgress">
              <div className="imgUploadProgressBar" style={{ width: `${attrs.progress}%` }} />
            </div>
          )}
        </div>
      ) : (
        <img
          ref={imgRef}
          src={attrs.src}
          alt={attrs.alt ?? ""}
          title={attrs.title ?? ""}
          data-asset-id={attrs.assetId ?? undefined}
          data-stock-image-id={attrs.stockImageId ?? undefined}
          style={{ width: "100%", display: "block", borderRadius: "var(--r)" }}
          draggable={false}
          onError={() => { if (isPending) setImgError(true); }}
          onLoad={() => setImgError(false)}
        />
      )}
      {selected && editor.isEditable && <div className="imgResizeHandle" onMouseDown={startResize} />}
    </NodeViewWrapper>
  );
}

const CustomImage = Image.extend({
  addAttributes() {
    return {
      ...this.parent?.(),
      assetId: {
        default: null,
        parseHTML: (el) => el.getAttribute("data-asset-id"),
        renderHTML: (attrs) => (attrs.assetId ? { "data-asset-id": attrs.assetId } : {}),
      },
      stockImageId: {
        default: null,
        parseHTML: (el) => {
          const value = el.getAttribute("data-stock-image-id");
          return value ? Number(value) : null;
        },
        renderHTML: (attrs) => (attrs.stockImageId ? { "data-stock-image-id": attrs.stockImageId } : {}),
      },
      width: {
        // 新插入的图片默认显示宽度（编辑器内）。已有文章在 content_json 里存了
        // 显式宽度、旧 HTML 也走下面的 parseHTML 回落，不受此默认值影响。
        default: "30%",
        parseHTML: (el) => el.style.width || "100%",
        renderHTML: (attrs) => ({ style: `width: ${attrs.width ?? "100%"}` }),
      },
      progress: {
        default: null,
        parseHTML: () => null,
        renderHTML: () => ({}),
      },
    };
  },
  addNodeView() {
    return ReactNodeViewRenderer(ImageResizeView);
  },
});

/**
 * 构建一套全新的 Tiptap 扩展实例。**每次调用返回全新实例**：Tiptap 扩展实例持有对
 * editor / storage 的引用，不能跨两个 editor 共享（会污染彼此状态），故所有扩展都经
 * `.configure()` 返回新副本、绝不复用同一个模块级对象。
 *
 * Tiptap v3 的 StarterKit 已内置 link / underline（这里只关掉 link 的点击跳转），
 * 故**不再单独注册** @tiptap/extension-link / @tiptap/extension-underline——重复注册会触发
 * "Duplicate extension names" 冲突，导致含链接/下划线标记的文档 setContent 解析失败、正文渲染为空。
 */
export function buildReadonlyExtensions() {
  return [
    StarterKit.configure({ link: { openOnClick: false } }),
    CustomImage.configure({ allowBase64: false }),
    CustomTextStyle.configure(),
    Color.configure(),
    Highlight.configure({ multicolor: true }),
    TextAlign.configure({ types: ["heading", "paragraph"] }),
  ];
}
