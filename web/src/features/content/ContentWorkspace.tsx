import { useEffect, useMemo, useRef, useState } from "react";
import { EditorContent, NodeViewWrapper, ReactNodeViewRenderer, useEditor } from "@tiptap/react";
import type { NodeViewProps } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import { TextStyle } from "@tiptap/extension-text-style";
import Color from "@tiptap/extension-color";
import Highlight from "@tiptap/extension-highlight";
import Underline from "@tiptap/extension-underline";
import TextAlign from "@tiptap/extension-text-align";
import { FixedSizeList as VirtualList } from "react-window";
import { Plus, Save, Search, Trash2, Upload, ChevronRight } from "lucide-react";
import { api, assetSrc, countWords, emptyDoc } from "../../api/client";
import type { Article, ArticleGroup, Asset, Draft } from "../../types";
import { ITEM_HEIGHT } from "../../types";
import { EditorToolbar } from "../../components/editor/EditorToolbar";
import { ArticleListItem } from "../../components/ArticleListItem";

function makeEmptyDraft(): Draft {
  return {
    id: null,
    title: "",
    author: "",
    cover_asset_id: null,
    status: "draft",
  };
}

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

function ImageResizeView({ node, updateAttributes, selected }: NodeViewProps) {
  const attrs = node.attrs as {
    src: string;
    alt: string;
    title: string;
    assetId: string | null;
    width: string;
  };
  const imgRef = useRef<HTMLImageElement>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

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
      <img
        ref={imgRef}
        src={attrs.src}
        alt={attrs.alt ?? ""}
        title={attrs.title ?? ""}
        data-asset-id={attrs.assetId ?? undefined}
        style={{ width: "100%", display: "block", borderRadius: "var(--r)" }}
        draggable={false}
      />
      {selected && <div className="imgResizeHandle" onMouseDown={startResize} />}
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
      width: {
        default: "100%",
        parseHTML: (el) => el.style.width || "100%",
        renderHTML: (attrs) => ({ style: `width: ${attrs.width ?? "100%"}` }),
      },
    };
  },
  addNodeView() {
    return ReactNodeViewRenderer(ImageResizeView);
  },
});

export function ContentWorkspace() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [groups, setGroups] = useState<ArticleGroup[]>([]);
  const [selectedArticleIds, setSelectedArticleIds] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<Draft>(makeEmptyDraft);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [editingGroupId, setEditingGroupId] = useState<number | null>(null);
  const [expandedGroupIds, setExpandedGroupIds] = useState<Set<number>>(new Set());

  const pasteImageRef = useRef<(file: File) => void>(() => {});
  const listContainerRef = useRef<HTMLDivElement>(null);
  const [listHeight, setListHeight] = useState(400);

  useEffect(() => {
    function measure() {
      if (listContainerRef.current) {
        setListHeight(listContainerRef.current.offsetHeight);
      }
    }
    measure();
    const observer = new ResizeObserver(measure);
    if (listContainerRef.current) observer.observe(listContainerRef.current);
    return () => observer.disconnect();
  }, []);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Link.configure({ openOnClick: false }),
      CustomImage.configure({ allowBase64: false }),
      CustomTextStyle,
      Color,
      Highlight.configure({ multicolor: true }),
      Underline,
      TextAlign.configure({ types: ["heading", "paragraph"] }),
    ],
    content: emptyDoc,
    editorProps: {
      attributes: {
        class: "editorSurface",
      },
      transformPastedHTML(html) {
        return html.replace(/ style="[^"]*"/gi, "");
      },
      handlePaste(_, event) {
        const items = Array.from(event.clipboardData?.items ?? []);
        const imageItem = items.find((item) => item.type.startsWith("image/"));
        if (!imageItem) return false;
        const file = imageItem.getAsFile();
        if (!file) return false;
        pasteImageRef.current(file);
        return true;
      },
    },
  });

  const selectedArticle = useMemo(
    () => articles.find((article) => article.id === draft.id) ?? null,
    [articles, draft.id],
  );

  const groupedArticleIdSet = useMemo(() => {
    const ids = new Set<number>();
    for (const g of groups) for (const item of g.items) ids.add(item.article_id);
    return ids;
  }, [groups]);

  const unifiedList = useMemo(() => {
    const articleById = Object.fromEntries(articles.map((a) => [a.id, a]));
    const items: Array<
      | { type: "article"; article: Article; sortTime: number }
      | { type: "group"; group: ArticleGroup; sortTime: number }
    > = [];
    for (const article of articles) {
      if (!groupedArticleIdSet.has(article.id)) {
        items.push({ type: "article", article, sortTime: new Date(article.updated_at).getTime() });
      }
    }
    for (const group of groups) {
      const times = group.items
        .map((item) => articleById[item.article_id]?.updated_at)
        .filter((t): t is string => !!t)
        .map((t) => new Date(t).getTime());
      items.push({ type: "group", group, sortTime: times.length ? Math.max(...times) : 0 });
    }
    return items.sort((a, b) => b.sortTime - a.sortTime);
  }, [articles, groups, groupedArticleIdSet]);

  const freeArticles = useMemo(() => {
    return articles
      .filter((a) => !groupedArticleIdSet.has(a.id))
      .sort((a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime());
  }, [articles, groupedArticleIdSet]);

  async function refreshArticles(nextQuery = query) {
    const params = nextQuery ? `?q=${encodeURIComponent(nextQuery)}` : "";
    const data = await api<Article[]>(`/api/articles${params}`);
    setArticles(data);
  }

  async function refreshGroups() {
    const data = await api<ArticleGroup[]>("/api/article-groups");
    setGroups(data);
  }

  useEffect(() => {
    void refreshArticles();
    void refreshGroups();
  }, []);

  function resetDraft() {
    setDraft(makeEmptyDraft());
    editor?.commands.setContent(emptyDoc);
    setSelectedArticleIds([]);
  }

  function loadArticle(article: Article) {
    setDraft({
      id: article.id,
      title: article.title,
      author: article.author ?? "",
      cover_asset_id: article.cover_asset_id,
      status: article.status,
    });
    editor?.commands.setContent(article.content_json);
  }

  async function uploadAsset(file: File): Promise<Asset> {
    const form = new FormData();
    form.append("file", file);
    return api<Asset>("/api/assets", { method: "POST", body: form });
  }

  async function handleCoverUpload(file: File | null) {
    if (!file) return;
    setLoading(true);
    try {
      const asset = await uploadAsset(file);
      setDraft((current) => ({ ...current, cover_asset_id: asset.id }));
      setNotice("封面已上传");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "封面上传失败");
    } finally {
      setLoading(false);
    }
  }

  async function handleBodyImageUpload(file: File | null) {
    if (!file || !editor) return;
    setLoading(true);
    try {
      const asset = await uploadAsset(file);
      editor
        .chain()
        .focus()
        .insertContent({
          type: "image",
          attrs: {
            src: asset.url,
            alt: asset.filename,
            title: asset.filename,
            assetId: asset.id,
          },
        })
        .run();
      setNotice("正文图片已插入");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "正文图片上传失败");
    } finally {
      setLoading(false);
    }
  }
  pasteImageRef.current = (file: File) => void handleBodyImageUpload(file);

  async function saveArticle() {
    if (!editor || !draft.title.trim()) {
      setNotice("标题不能为空");
      return;
    }
    setLoading(true);
    try {
      const payload = {
        title: draft.title.trim(),
        author: draft.author.trim() || null,
        cover_asset_id: draft.cover_asset_id,
        content_json: editor.getJSON(),
        content_html: editor.getHTML(),
        plain_text: editor.getText(),
        word_count: countWords(editor.getText()),
        status: draft.status,
      };
      const saved = draft.id
        ? await api<Article>(`/api/articles/${draft.id}`, { method: "PUT", body: JSON.stringify(payload) })
        : await api<Article>("/api/articles", { method: "POST", body: JSON.stringify(payload) });
      await refreshArticles();
      resetDraft();
      setNotice("文章已保存");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存失败");
    } finally {
      setLoading(false);
    }
  }

  async function deleteCurrentArticle() {
    if (!draft.id) return;
    setLoading(true);
    try {
      await api<void>(`/api/articles/${draft.id}`, { method: "DELETE" });
      resetDraft();
      await refreshArticles();
      setNotice("文章已删除");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除失败");
    } finally {
      setLoading(false);
    }
  }

  async function saveGroupFromSelection() {
    const name = groupName.trim();
    if (!name || selectedArticleIds.length === 0) {
      setNotice("请输入分组名称并选择文章");
      return;
    }
    setLoading(true);
    try {
      const group = editingGroupId
        ? await api<ArticleGroup>(`/api/article-groups/${editingGroupId}`, {
            method: "PUT",
            body: JSON.stringify({ name }),
          })
        : await api<ArticleGroup>("/api/article-groups", {
            method: "POST",
            body: JSON.stringify({ name }),
          });
      await api<ArticleGroup>(`/api/article-groups/${group.id}/items`, {
        method: "PUT",
        body: JSON.stringify({
          items: selectedArticleIds.map((articleId, index) => ({ article_id: articleId, sort_order: index })),
        }),
      });
      setGroupName("");
      setEditingGroupId(null);
      setSelectedArticleIds([]);
      await refreshGroups();
      setNotice(editingGroupId ? "分组已更新" : "分组已创建");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "保存分组失败");
    } finally {
      setLoading(false);
    }
  }

  async function deleteEditingGroup() {
    if (!editingGroupId) return;
    setLoading(true);
    try {
      await api<void>(`/api/article-groups/${editingGroupId}`, { method: "DELETE" });
      setEditingGroupId(null);
      setGroupName("");
      setSelectedArticleIds([]);
      await refreshGroups();
      setNotice("分组已删除");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除分组失败");
    } finally {
      setLoading(false);
    }
  }

  function loadGroup(group: ArticleGroup) {
    setEditingGroupId(group.id);
    setGroupName(group.name);
    setSelectedArticleIds(group.items.sort((a, b) => a.sort_order - b.sort_order).map((item) => item.article_id));
  }

  function toggleSelectedArticle(articleId: number) {
    setSelectedArticleIds((current) =>
      current.includes(articleId) ? current.filter((id) => id !== articleId) : [...current, articleId],
    );
  }

  async function searchArticles() {
    await refreshArticles(query);
  }

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">内容管理</p>
          <h1>图文工作台</h1>
        </div>
        <div className="topActions">
          {notice ? <span className="status">{notice}</span> : null}
          <button className="dangerButton" disabled={!draft.id || loading} type="button" onClick={() => void deleteCurrentArticle()}>
            <Trash2 size={16} />
            删除
          </button>
          <button className="primaryButton" disabled={loading} type="button" onClick={() => void saveArticle()}>
            <Save size={16} />
            保存
          </button>
          <button className="secondaryButton" disabled={loading} type="button" onClick={resetDraft}>
            <Plus size={16} />
            新建
          </button>
        </div>
      </header>

      <section className="contentGrid">
        <aside className="listPane">
          <div className="searchRow">
            <Search size={16} />
            <input value={query} placeholder="搜索标题或作者" onChange={(event) => setQuery(event.target.value)} />
            <button type="button" onClick={searchArticles}>
              搜索
            </button>
          </div>

          <div className="articleList">
            <div ref={listContainerRef} style={{ minHeight: freeArticles.length > 0 ? 200 : 0 }}>
              {freeArticles.length > 0 ? (
                <VirtualList height={Math.min(listHeight, freeArticles.length * ITEM_HEIGHT)} itemCount={freeArticles.length} itemSize={ITEM_HEIGHT} width="100%">
                  {({ index, style }) => {
                    const article = freeArticles[index];
                    return (
                      <div style={style}>
                        <ArticleListItem
                          article={article}
                          draftId={draft.id}
                          selectedIds={selectedArticleIds}
                          onToggle={toggleSelectedArticle}
                          onSelect={loadArticle}
                        />
                      </div>
                    );
                  }}
                </VirtualList>
              ) : null}
            </div>
            {unifiedList.filter((item) => item.type === "group").map((item) => {
              const { group } = item;
              const isExpanded = expandedGroupIds.has(group.id);
              const groupArticles = group.items
                .slice()
                .sort((a, b) => a.sort_order - b.sort_order)
                .map((gi) => articles.find((a) => a.id === gi.article_id))
                .filter((a): a is Article => a !== undefined);
              return (
                <div className="groupRowItem" key={`g-${group.id}`}>
                  <div className={`groupRowHeader ${group.id === editingGroupId ? "selected" : ""}`}>
                    <button
                      className="groupRowToggle"
                      type="button"
                      onClick={() =>
                        setExpandedGroupIds((prev) => {
                          const next = new Set(prev);
                          if (next.has(group.id)) next.delete(group.id);
                          else next.add(group.id);
                          return next;
                        })
                      }
                    >
                      <ChevronRight size={13} className={`groupRowChevron${isExpanded ? " open" : ""}`} />
                      <span className="groupRowName">{group.name}</span>
                      <small className="groupRowCount">{group.items.length} 篇</small>
                    </button>
                    <button className="groupRowEdit" type="button" onClick={() => loadGroup(group)}>
                      编辑
                    </button>
                  </div>
                  {isExpanded ? (
                    <div className="groupRowArticles">
                      {groupArticles.map((article) => (
                        <article
                          className={`articleItem ${article.id === draft.id ? "selected" : ""}`}
                          key={article.id}
                        >
                          <label className="checkLine">
                            <input
                              checked={selectedArticleIds.includes(article.id)}
                              type="checkbox"
                              onChange={() => toggleSelectedArticle(article.id)}
                            />
                            <span>{article.status}</span>
                          </label>
                          <button type="button" onClick={() => loadArticle(article)}>
                            <strong>{article.title}</strong>
                            <span>{article.author || "未填写作者"}</span>
                            <small>
                              {new Date(article.updated_at).toLocaleString()}
                              {article.published_count > 0 ? <span style={{ color: "#16a34a", marginLeft: 6 }}>· 已发布 {article.published_count} 次</span> : null}
                            </small>
                          </button>
                        </article>
                      ))}
                      {groupArticles.length === 0 ? <p className="emptyText">分组暂无文章</p> : null}
                    </div>
                  ) : null}
                </div>
              );
            })}
            {freeArticles.length === 0 && groups.length === 0 ? <p className="emptyText">暂无文章</p> : null}
          </div>

          <section className="groupBox">
            <h2>文章分组</h2>
            <p style={{ fontSize: 12, color: "#64748b", margin: "0 0 8px" }}>
              {editingGroupId ? "勾选左侧文章后点「更新」可增减分组内文章" : "勾选左侧文章后填写名称，点「创建」"}
            </p>
            <div className="groupCreate">
              <input value={groupName} placeholder="分组名称" onChange={(event) => setGroupName(event.target.value)} />
              <button type="button" onClick={saveGroupFromSelection}>
                {editingGroupId ? "更新" : "创建"}
              </button>
            </div>
            {editingGroupId ? (
              <div className="groupEditActions">
                <button type="button" onClick={() => { setEditingGroupId(null); setGroupName(""); setSelectedArticleIds([]); }}>
                  取消编辑
                </button>
                <button type="button" onClick={deleteEditingGroup}>
                  删除分组
                </button>
              </div>
            ) : null}
          </section>
        </aside>

        <section className="editorPane">
          <div className="formRow split">
            <label>
              标题
              <input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} />
            </label>
            <label>
              作者
              <input value={draft.author} onChange={(event) => setDraft({ ...draft, author: event.target.value })} />
            </label>
            <label>
              状态
              <select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
                <option value="draft">草稿</option>
                <option value="ready">待发布</option>
                <option value="archived">归档</option>
              </select>
            </label>
          </div>

          <section className="coverRow">
            <div className="coverPreview">
              {assetSrc(draft.cover_asset_id) ? <img alt="封面" src={assetSrc(draft.cover_asset_id) ?? ""} /> : <span>封面</span>}
            </div>
            <label className="fileButton">
              <Upload size={16} />
              上传封面
              <input accept="image/*" type="file" onChange={(event) => void handleCoverUpload(event.target.files?.[0] ?? null)} />
            </label>
            {selectedArticle ? <span className="metaText">正文图片 {selectedArticle.body_assets.length} 张</span> : null}
          </section>

          <EditorToolbar editor={editor} onImageUpload={handleBodyImageUpload} />
          <div className="editorWrap">
            <EditorContent editor={editor} />
          </div>

        </section>
      </section>
    </>
  );
}
