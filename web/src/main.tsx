import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import Link from "@tiptap/extension-link";
import {
  Bold,
  CheckCircle2,
  Download,
  FileText,
  Heading1,
  Heading2,
  ImagePlus,
  Italic,
  LinkIcon,
  List,
  ListOrdered,
  MonitorCog,
  Plus,
  Quote,
  RadioTower,
  RefreshCw,
  Save,
  Search,
  Send,
  Trash2,
  Upload,
  UserPlus,
} from "lucide-react";
import "./styles.css";

type NavKey = "content" | "media" | "tasks" | "system";

type Asset = {
  id: string;
  filename: string;
  mime_type: string;
  size: number;
  width: number | null;
  height: number | null;
  url: string;
};

type ArticleBodyAsset = {
  asset_id: string;
  position: number;
  editor_node_id: string | null;
};

type Article = {
  id: number;
  title: string;
  author: string | null;
  cover_asset_id: string | null;
  content_json: Record<string, unknown>;
  content_html: string;
  plain_text: string;
  word_count: number;
  status: string;
  body_assets: ArticleBodyAsset[];
  updated_at: string;
};

type ArticleGroup = {
  id: number;
  name: string;
  items: { article_id: number; sort_order: number }[];
};

type Account = {
  id: number;
  platform_code: string;
  platform_name: string;
  display_name: string;
  status: string;
  last_checked_at: string | null;
  last_login_at: string | null;
  state_path: string;
  note: string | null;
};

type Draft = {
  id: number | null;
  title: string;
  author: string;
  cover_asset_id: string | null;
  status: string;
};

const navItems: { key: NavKey; label: string; icon: React.ComponentType<{ size?: number }> }[] = [
  { key: "content", label: "内容管理", icon: FileText },
  { key: "media", label: "媒体矩阵", icon: RadioTower },
  { key: "tasks", label: "分发引擎", icon: Send },
  { key: "system", label: "系统状态", icon: MonitorCog },
];

const emptyDoc = { type: "doc", content: [{ type: "paragraph" }] };

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: init?.body instanceof FormData ? undefined : { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

function assetSrc(assetId: string | null): string | null {
  return assetId ? `/api/assets/${assetId}` : null;
}

function countWords(text: string): number {
  return text.replace(/\s+/g, "").length;
}

function makeEmptyDraft(): Draft {
  return {
    id: null,
    title: "",
    author: "",
    cover_asset_id: null,
    status: "draft",
  };
}

function App() {
  const [activeNav, setActiveNav] = useState<NavKey>("content");

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">Geo 协作平台</div>
        <nav className="nav">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                className={`navItem ${activeNav === item.key ? "active" : ""}`}
                key={item.key}
                type="button"
                onClick={() => setActiveNav(item.key)}
              >
                <Icon size={18} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
      </aside>
      <section className="workspace">
        {activeNav === "content" ? <ContentWorkspace /> : null}
        {activeNav === "media" ? <MediaWorkspace /> : null}
        {activeNav === "tasks" || activeNav === "system" ? (
          <Placeholder title={navItems.find((item) => item.key === activeNav)?.label ?? ""} />
        ) : null}
      </section>
    </main>
  );
}

function Placeholder({ title }: { title: string }) {
  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">Week 2</p>
          <h1>{title}</h1>
        </div>
      </header>
      <section className="panel">
        <h2>待实现</h2>
        <p>当前阶段先完成内容管理闭环，后续任务会继续补齐该页面。</p>
      </section>
    </>
  );
}

function MediaWorkspace() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [displayName, setDisplayName] = useState("头条号账号");
  const [accountKey, setAccountKey] = useState("chrome-spike");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  async function refreshAccounts() {
    const data = await api<Account[]>("/api/accounts");
    setAccounts(data);
  }

  useEffect(() => {
    void refreshAccounts();
  }, []);

  async function login(useBrowser: boolean) {
    setLoading(true);
    setNotice(useBrowser ? "已打开浏览器，请完成登录" : "正在复用已保存状态");
    try {
      await api<Account>("/api/accounts/toutiao/login", {
        method: "POST",
        body: JSON.stringify({
          display_name: displayName,
          account_key: accountKey,
          channel: "chrome",
          wait_seconds: 180,
          use_browser: useBrowser,
        }),
      });
      await refreshAccounts();
      setNotice("账号已添加");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "添加账号失败");
    } finally {
      setLoading(false);
    }
  }

  async function check(account: Account) {
    setLoading(true);
    try {
      await api<Account>(`/api/accounts/${account.id}/check`, {
        method: "POST",
        body: JSON.stringify({ channel: "chrome", wait_seconds: 30, use_browser: true }),
      });
      await refreshAccounts();
      setNotice("校验完成");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "校验失败");
    } finally {
      setLoading(false);
    }
  }

  async function relogin(account: Account) {
    setLoading(true);
    setNotice("已打开浏览器，请完成重新登录");
    try {
      await api<Account>(`/api/accounts/${account.id}/relogin`, {
        method: "POST",
        body: JSON.stringify({ channel: "chrome", wait_seconds: 180, use_browser: true }),
      });
      await refreshAccounts();
      setNotice("重新登录完成");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "重新登录失败");
    } finally {
      setLoading(false);
    }
  }

  async function remove(account: Account) {
    setLoading(true);
    try {
      await api<void>(`/api/accounts/${account.id}`, { method: "DELETE" });
      await refreshAccounts();
      setNotice("账号已删除");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "删除失败");
    } finally {
      setLoading(false);
    }
  }

  async function exportAuthPackage() {
    setLoading(true);
    try {
      const response = await fetch("/api/accounts/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_ids: accounts.map((account) => account.id) }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `${response.status} ${response.statusText}`);
      }

      const blob = await response.blob();
      const disposition = response.headers.get("content-disposition") ?? "";
      const match = disposition.match(/filename="?([^"]+)"?/);
      const filename = match?.[1] ?? `geo-auth-export-${Date.now()}.zip`;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setNotice("授权包已导出");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "导出授权包失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">媒体矩阵</p>
          <h1>头条号授权</h1>
        </div>
        <div className="topActions">
          {notice ? <span className="status">{notice}</span> : null}
          <button className="secondaryButton" disabled={loading || accounts.length === 0} type="button" onClick={() => void exportAuthPackage()}>
            <Download size={16} />
            导出授权包
          </button>
        </div>
      </header>

      <section className="mediaGrid">
        <section className="accountForm">
          <h2>添加头条号</h2>
          <label>
            显示名称
            <input value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
          </label>
          <label>
            本地状态目录
            <input value={accountKey} onChange={(event) => setAccountKey(event.target.value)} />
          </label>
          <div className="accountActions">
            <button className="primaryButton" disabled={loading} type="button" onClick={() => void login(true)}>
              <UserPlus size={16} />
              添加授权
            </button>
            <button className="secondaryButton" disabled={loading} type="button" onClick={() => void login(false)}>
              <CheckCircle2 size={16} />
              复用状态
            </button>
          </div>
        </section>

        <section className="accountList">
          {accounts.map((account) => (
            <article className="accountCard" key={account.id}>
              <div>
                <strong>{account.display_name}</strong>
                <span>{account.platform_name}</span>
              </div>
              <span className={`badge ${account.status}`}>{account.status}</span>
              <small>{account.state_path}</small>
              <div className="accountCardActions">
                <button type="button" disabled={loading} onClick={() => void check(account)}>
                  <CheckCircle2 size={15} />
                  校验
                </button>
                <button type="button" disabled={loading} onClick={() => void relogin(account)}>
                  <RefreshCw size={15} />
                  重登
                </button>
                <button type="button" disabled={loading} onClick={() => void remove(account)}>
                  <Trash2 size={15} />
                  删除
                </button>
              </div>
            </article>
          ))}
          {accounts.length === 0 ? <p className="emptyText">暂无授权账号</p> : null}
        </section>
      </section>
    </>
  );
}

function ContentWorkspace() {
  const [articles, setArticles] = useState<Article[]>([]);
  const [groups, setGroups] = useState<ArticleGroup[]>([]);
  const [selectedArticleIds, setSelectedArticleIds] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<Draft>(makeEmptyDraft);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [groupName, setGroupName] = useState("");
  const [editingGroupId, setEditingGroupId] = useState<number | null>(null);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Link.configure({ openOnClick: false }),
      Image.configure({ allowBase64: false }),
    ],
    content: emptyDoc,
    editorProps: {
      attributes: {
        class: "editorSurface",
      },
    },
  });

  const selectedArticle = useMemo(
    () => articles.find((article) => article.id === draft.id) ?? null,
    [articles, draft.id],
  );

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
      setDraft({
        id: saved.id,
        title: saved.title,
        author: saved.author ?? "",
        cover_asset_id: saved.cover_asset_id,
        status: saved.status,
      });
      editor.commands.setContent(saved.content_json);
      await refreshArticles();
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
          <button className="primaryButton" disabled={loading} type="button" onClick={resetDraft}>
            <Plus size={16} />
            新建图文
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
            {articles.map((article) => (
              <article className={`articleItem ${article.id === draft.id ? "selected" : ""}`} key={article.id}>
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
                  <small>{new Date(article.updated_at).toLocaleString()}</small>
                </button>
              </article>
            ))}
            {articles.length === 0 ? <p className="emptyText">暂无文章</p> : null}
          </div>

          <section className="groupBox">
            <h2>文章分组</h2>
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
            <div className="groupList">
              {groups.map((group) => (
                <button className={`groupItem ${group.id === editingGroupId ? "selected" : ""}`} key={group.id} type="button" onClick={() => loadGroup(group)}>
                  <span>{group.name}</span>
                  <small>{group.items.length} 篇</small>
                </button>
              ))}
              {groups.length === 0 ? <p className="emptyText">暂无分组</p> : null}
            </div>
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

          <div className="bottomActions">
            <button className="primaryButton" disabled={loading} type="button" onClick={() => void saveArticle()}>
              <Save size={16} />
              保存
            </button>
            <button className="dangerButton" disabled={!draft.id || loading} type="button" onClick={() => void deleteCurrentArticle()}>
              <Trash2 size={16} />
              删除
            </button>
          </div>
        </section>
      </section>
    </>
  );
}

function EditorToolbar({
  editor,
  onImageUpload,
}: {
  editor: ReturnType<typeof useEditor>;
  onImageUpload: (file: File | null) => Promise<void>;
}) {
  if (!editor) return null;

  return (
    <div className="toolbar">
      <button className={editor.isActive("bold") ? "active" : ""} title="加粗" type="button" onClick={() => editor.chain().focus().toggleBold().run()}>
        <Bold size={16} />
      </button>
      <button className={editor.isActive("italic") ? "active" : ""} title="斜体" type="button" onClick={() => editor.chain().focus().toggleItalic().run()}>
        <Italic size={16} />
      </button>
      <button title="一级标题" type="button" onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()}>
        <Heading1 size={16} />
      </button>
      <button title="二级标题" type="button" onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()}>
        <Heading2 size={16} />
      </button>
      <button title="无序列表" type="button" onClick={() => editor.chain().focus().toggleBulletList().run()}>
        <List size={16} />
      </button>
      <button title="有序列表" type="button" onClick={() => editor.chain().focus().toggleOrderedList().run()}>
        <ListOrdered size={16} />
      </button>
      <button title="引用" type="button" onClick={() => editor.chain().focus().toggleBlockquote().run()}>
        <Quote size={16} />
      </button>
      <button
        title="链接"
        type="button"
        onClick={() => {
          const url = window.prompt("链接地址");
          if (url) editor.chain().focus().setLink({ href: url }).run();
        }}
      >
        <LinkIcon size={16} />
      </button>
      <label className="toolbarFile" title="插入图片">
        <ImagePlus size={16} />
        <input accept="image/*" type="file" onChange={(event) => void onImageUpload(event.target.files?.[0] ?? null)} />
      </label>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
