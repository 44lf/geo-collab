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

type TaskAccountRead = {
  account_id: number;
  sort_order: number;
  display_name: string;
  status: string;
};

type Task = {
  id: number;
  name: string;
  task_type: string;
  status: string;
  platform_id: number;
  platform_code: string;
  article_id: number | null;
  group_id: number | null;
  stop_before_publish: boolean;
  accounts: TaskAccountRead[];
  record_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

type PublishRecord = {
  id: number;
  task_id: number;
  article_id: number;
  platform_id: number;
  account_id: number;
  status: string;
  publish_url: string | null;
  error_message: string | null;
  retry_of_record_id: number | null;
  started_at: string | null;
  finished_at: string | null;
};

type TaskLog = {
  id: number;
  task_id: number;
  record_id: number | null;
  level: string;
  message: string;
  screenshot_asset_id: string | null;
  created_at: string;
};

type AssignmentPreview = {
  task_type: string;
  platform_code: string;
  article_count: number;
  account_count: number;
  items: { position: number; article_id: number; account_id: number; account_sort_order: number }[];
};

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "待执行",
    running: "执行中",
    succeeded: "成功",
    partial_failed: "部分失败",
    failed: "失败",
    cancelled: "已取消",
    waiting_manual_publish: "等待确认",
  };
  return labels[status] ?? status;
}

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
        <div className="brand">
          <div className="brandMark" />
          <div className="brandBody">
            <span className="brandName">Geo</span>
            <span className="brandSub">协作平台</span>
          </div>
        </div>
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
                <Icon size={17} />
                <span>{item.label}</span>
                <span className="navDot" />
              </button>
            );
          })}
        </nav>
      </aside>
      <section className="workspace">
        <div key={activeNav} className="workspaceInner">
          {activeNav === "content" ? <ContentWorkspace /> : null}
          {activeNav === "media" ? <MediaWorkspace /> : null}
          {activeNav === "tasks" ? <TasksWorkspace /> : null}
          {activeNav === "system" ? <SystemWorkspace /> : null}
        </div>
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

type SystemStatus = {
  service: string;
  version: string;
  data_dir: string;
  database_path: string;
  directories_ready: boolean;
  article_count: number;
  account_count: number;
  task_count: number;
  browser_ready: boolean;
};

function SystemWorkspace() {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function refresh() {
    setLoading(true);
    try {
      const data = await api<SystemStatus>("/api/system/status");
      setStatus(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取系统状态失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refresh();
  }, []);

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">系统状态</p>
          <h1>运行信息</h1>
        </div>
        <div className="topActions">
          <button className="secondaryButton" type="button" disabled={loading} onClick={() => void refresh()}>
            <RefreshCw size={15} />
            刷新
          </button>
        </div>
      </header>

      {error ? (
        <div className="panel" style={{ borderColor: "var(--red-soft)", color: "var(--red)" }}>{error}</div>
      ) : null}

      {status ? (
        <div style={{ display: "grid", gap: 16, maxWidth: 760 }}>
          <div className="panel">
            <h2 style={{ marginBottom: 16 }}>服务</h2>
            <dl className="statGrid">
              <dt>状态</dt>
              <dd><span className="badge succeeded">✓ {status.service}</span></dd>
              <dt>版本</dt>
              <dd>{status.version}</dd>
              <dt>浏览器（Chrome）</dt>
              <dd>
                <span className={`badge ${status.browser_ready ? "succeeded" : "failed"}`}>
                  {status.browser_ready ? "已检测到" : "未找到"}
                </span>
              </dd>
            </dl>
          </div>

          <div className="panel">
            <h2 style={{ marginBottom: 16 }}>数据</h2>
            <dl className="statGrid">
              <dt>文章</dt>
              <dd>{status.article_count} 篇</dd>
              <dt>账号</dt>
              <dd>{status.account_count} 个</dd>
              <dt>任务</dt>
              <dd>{status.task_count} 个</dd>
              <dt>目录就绪</dt>
              <dd>
                <span className={`badge ${status.directories_ready ? "succeeded" : "failed"}`}>
                  {status.directories_ready ? "是" : "否"}
                </span>
              </dd>
            </dl>
          </div>

          <div className="panel">
            <h2 style={{ marginBottom: 16 }}>路径</h2>
            <dl className="statGrid">
              <dt>数据目录</dt>
              <dd style={{ fontFamily: "monospace", fontSize: 13, wordBreak: "break-all" }}>{status.data_dir}</dd>
              <dt>数据库</dt>
              <dd style={{ fontFamily: "monospace", fontSize: 13, wordBreak: "break-all" }}>{status.database_path}</dd>
            </dl>
          </div>
        </div>
      ) : loading ? (
        <p style={{ color: "#64748b" }}>加载中…</p>
      ) : null}
    </>
  );
}

const TERMINAL_STATUSES = new Set(["succeeded", "partial_failed", "failed", "cancelled"]);

function TasksWorkspace() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<number | null>(null);
  const [records, setRecords] = useState<PublishRecord[]>([]);
  const [logs, setLogs] = useState<TaskLog[]>([]);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [articles, setArticles] = useState<Article[]>([]);
  const [groups, setGroups] = useState<ArticleGroup[]>([]);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);

  const [formName, setFormName] = useState("");
  const [formType, setFormType] = useState<"single" | "group_round_robin">("single");
  const [formArticleId, setFormArticleId] = useState<number | null>(null);
  const [formGroupId, setFormGroupId] = useState<number | null>(null);
  const [formAccountIds, setFormAccountIds] = useState<number[]>([]);
  const [preview, setPreview] = useState<AssignmentPreview | null>(null);

  const selectedTask = tasks.find((t) => t.id === selectedTaskId) ?? null;
  const taskIsRunning = selectedTask?.status === "running";
  const articleMap = useMemo(() => Object.fromEntries(articles.map((a) => [a.id, a])), [articles]);
  const accountMap = useMemo(() => Object.fromEntries(accounts.map((a) => [a.id, a])), [accounts]);

  useEffect(() => {
    void loadInitial();
  }, []);

  useEffect(() => {
    if (!selectedTaskId || !taskIsRunning) return;
    const interval = setInterval(() => {
      void refreshDetail(selectedTaskId);
    }, 2500);
    return () => clearInterval(interval);
  }, [selectedTaskId, taskIsRunning]);

  async function loadInitial() {
    const [ts, accs, arts, gs] = await Promise.all([
      api<Task[]>("/api/tasks"),
      api<Account[]>("/api/accounts"),
      api<Article[]>("/api/articles"),
      api<ArticleGroup[]>("/api/article-groups"),
    ]);
    setTasks(ts);
    setAccounts(accs);
    setArticles(arts);
    setGroups(gs);
  }

  async function refreshDetail(taskId: number) {
    const [rs, ls, ts] = await Promise.all([
      api<PublishRecord[]>(`/api/tasks/${taskId}/records`),
      api<TaskLog[]>(`/api/tasks/${taskId}/logs`),
      api<Task[]>("/api/tasks"),
    ]);
    setRecords(rs);
    setLogs(ls);
    setTasks(ts);
  }

  async function selectTask(taskId: number) {
    setSelectedTaskId(taskId);
    await refreshDetail(taskId);
  }

  async function createTask() {
    if (!formName.trim() || formAccountIds.length === 0) {
      setNotice("请填写任务名称并选择账号");
      return;
    }
    if (formType === "single" && !formArticleId) {
      setNotice("请选择文章");
      return;
    }
    if (formType === "group_round_robin" && !formGroupId) {
      setNotice("请选择分组");
      return;
    }
    setLoading(true);
    try {
      const task = await api<Task>("/api/tasks", {
        method: "POST",
        body: JSON.stringify({
          name: formName.trim(),
          task_type: formType,
          article_id: formType === "single" ? formArticleId : null,
          group_id: formType === "group_round_robin" ? formGroupId : null,
          accounts: formAccountIds.map((id, index) => ({ account_id: id, sort_order: index })),
        }),
      });
      setShowCreateForm(false);
      setFormName("");
      setFormArticleId(null);
      setFormGroupId(null);
      setFormAccountIds([]);
      setPreview(null);
      setNotice("任务已创建");
      await selectTask(task.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "创建失败");
    } finally {
      setLoading(false);
    }
  }

  async function loadPreview() {
    if (!formGroupId || formAccountIds.length === 0) return;
    setLoading(true);
    try {
      const result = await api<AssignmentPreview>("/api/tasks/preview", {
        method: "POST",
        body: JSON.stringify({
          name: formName || "预览",
          task_type: "group_round_robin",
          group_id: formGroupId,
          accounts: formAccountIds.map((id, index) => ({ account_id: id, sort_order: index })),
        }),
      });
      setPreview(result);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "预览失败");
    } finally {
      setLoading(false);
    }
  }

  async function executeTask() {
    if (!selectedTaskId) return;
    setLoading(true);
    try {
      await api<Task>(`/api/tasks/${selectedTaskId}/execute`, { method: "POST" });
      await refreshDetail(selectedTaskId);
      setNotice("已启动");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "启动失败");
    } finally {
      setLoading(false);
    }
  }

  async function cancelTask() {
    if (!selectedTaskId) return;
    setLoading(true);
    try {
      await api<Task>(`/api/tasks/${selectedTaskId}/cancel`, { method: "POST" });
      await refreshDetail(selectedTaskId);
      setNotice("已取消");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "取消失败");
    } finally {
      setLoading(false);
    }
  }

  async function retryRecord(recordId: number) {
    setLoading(true);
    try {
      await api<PublishRecord>(`/api/publish-records/${recordId}/retry`, { method: "POST" });
      if (selectedTaskId) await refreshDetail(selectedTaskId);
      setNotice("已创建重试记录");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "重试失败");
    } finally {
      setLoading(false);
    }
  }

  function toggleAccount(accountId: number) {
    setFormAccountIds((prev) =>
      prev.includes(accountId) ? prev.filter((id) => id !== accountId) : [...prev, accountId],
    );
    setPreview(null);
  }

  const validAccounts = accounts.filter((a) => a.status === "valid");
  const canExecute = selectedTask && !TERMINAL_STATUSES.has(selectedTask.status);
  const canCancel = selectedTask && (selectedTask.status === "running" || selectedTask.status === "pending");

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">分发引擎</p>
          <h1>任务管理</h1>
        </div>
        <div className="topActions">
          {notice ? <span className="status">{notice}</span> : null}
        </div>
      </header>

      <section className="taskGrid">
        <div className="listPane">
          <button
            className="primaryButton"
            style={{ width: "100%", marginBottom: 12 }}
            type="button"
            onClick={() => { setShowCreateForm((v) => !v); setPreview(null); }}
          >
            <Plus size={16} />
            {showCreateForm ? "收起" : "创建任务"}
          </button>

          {showCreateForm ? (
            <div className="createForm">
              <label>
                任务名称
                <input value={formName} placeholder="例如：头条号5月第一批" onChange={(e) => setFormName(e.target.value)} />
              </label>
              <label>
                任务类型
                <select
                  value={formType}
                  onChange={(e) => { setFormType(e.target.value as "single" | "group_round_robin"); setPreview(null); }}
                >
                  <option value="single">单篇发布</option>
                  <option value="group_round_robin">分组轮询</option>
                </select>
              </label>
              {formType === "single" ? (
                <label>
                  文章
                  <select
                    value={formArticleId ?? ""}
                    onChange={(e) => setFormArticleId(Number(e.target.value) || null)}
                  >
                    <option value="">请选择文章</option>
                    {articles.map((a) => (
                      <option key={a.id} value={a.id}>{a.title}</option>
                    ))}
                  </select>
                </label>
              ) : (
                <label>
                  文章分组
                  <select
                    value={formGroupId ?? ""}
                    onChange={(e) => { setFormGroupId(Number(e.target.value) || null); setPreview(null); }}
                  >
                    <option value="">请选择分组</option>
                    {groups.map((g) => (
                      <option key={g.id} value={g.id}>{g.name}（{g.items.length} 篇）</option>
                    ))}
                  </select>
                </label>
              )}
              <div>
                <p style={{ margin: "0 0 6px", fontSize: 13, color: "#475569" }}>发布账号</p>
                {validAccounts.map((a) => (
                  <label key={a.id} className="checkLine">
                    <input type="checkbox" checked={formAccountIds.includes(a.id)} onChange={() => toggleAccount(a.id)} />
                    <span>{a.display_name}</span>
                  </label>
                ))}
                {validAccounts.length === 0 ? <p className="emptyText">暂无有效账号</p> : null}
              </div>
              {formType === "group_round_robin" && formGroupId && formAccountIds.length > 0 ? (
                <button className="secondaryButton" style={{ width: "100%" }} type="button" disabled={loading} onClick={() => void loadPreview()}>
                  预览分配
                </button>
              ) : null}
              {preview ? (
                <div className="previewBox">
                  <p style={{ margin: "0 0 6px", fontSize: 13, color: "#475569" }}>
                    {preview.article_count} 篇 · {preview.account_count} 个账号
                  </p>
                  {preview.items.map((item) => (
                    <div key={item.position} className="previewRow">
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {articleMap[item.article_id]?.title ?? `文章 ${item.article_id}`}
                      </span>
                      <span style={{ flexShrink: 0, color: "#64748b" }}>
                        {accountMap[item.account_id]?.display_name ?? `账号 ${item.account_id}`}
                      </span>
                    </div>
                  ))}
                </div>
              ) : null}
              <button className="primaryButton" style={{ width: "100%" }} type="button" disabled={loading} onClick={() => void createTask()}>
                创建任务
              </button>
            </div>
          ) : null}

          <div className="articleList">
            {tasks.map((task) => (
              <button
                key={task.id}
                className={`taskItem ${task.id === selectedTaskId ? "selected" : ""}`}
                type="button"
                onClick={() => void selectTask(task.id)}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <strong style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                    {task.name}
                  </strong>
                  <span className={`badge ${task.status}`}>{statusLabel(task.status)}</span>
                </div>
                <small style={{ color: "#64748b", fontSize: 12 }}>
                  {task.task_type === "single" ? "单篇" : "分组轮询"} · {task.record_count} 条 · {new Date(task.created_at).toLocaleDateString()}
                </small>
              </button>
            ))}
            {tasks.length === 0 ? <p className="emptyText">暂无任务</p> : null}
          </div>
        </div>

        {selectedTask ? (
          <div className="taskDetail">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 14 }}>
              <div>
                <h2 style={{ margin: "0 0 4px" }}>{selectedTask.name}</h2>
                <small style={{ color: "#64748b", fontSize: 13 }}>
                  {selectedTask.task_type === "single" ? "单篇发布" : "分组轮询"} · 头条号
                  {selectedTask.started_at ? ` · 开始于 ${new Date(selectedTask.started_at).toLocaleString()}` : ""}
                </small>
              </div>
              <span className={`badge ${selectedTask.status}`}>{statusLabel(selectedTask.status)}</span>
            </div>

            <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
              {canExecute ? (
                <button className="primaryButton" type="button" disabled={loading} onClick={() => void executeTask()}>
                  <Send size={15} />
                  执行
                </button>
              ) : null}
              {canCancel ? (
                <button className="dangerButton" type="button" disabled={loading} onClick={() => void cancelTask()}>
                  取消任务
                </button>
              ) : null}
            </div>

            <hr className="sectionDivider" />
            <h2 style={{ marginBottom: 12 }}>发布记录</h2>
            <div style={{ display: "grid", gap: 10, marginBottom: 20 }}>
              {records.map((record) => {
                const article = articleMap[record.article_id];
                const account = accountMap[record.account_id];
                return (
                  <div key={record.id} className="recordItem">
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8 }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }}>
                        {article?.title ?? `文章 ${record.article_id}`}
                      </span>
                      <span className={`badge ${record.status}`}>{statusLabel(record.status)}</span>
                    </div>
                    <small style={{ color: "#64748b", fontSize: 13 }}>
                      {account?.display_name ?? `账号 ${record.account_id}`}
                      {record.retry_of_record_id ? ` · 重试自 #${record.retry_of_record_id}` : ""}
                    </small>
                    {record.publish_url ? (
                      <small>
                        <a href={record.publish_url} target="_blank" rel="noreferrer" style={{ color: "#214f7a" }}>
                          查看已发布链接
                        </a>
                      </small>
                    ) : null}
                    {record.error_message ? (
                      <small style={{ color: "#dc2626" }}>{record.error_message}</small>
                    ) : null}

                    {record.status === "failed" ? (
                      <button
                        className="secondaryButton"
                        type="button"
                        disabled={loading}
                        style={{ justifySelf: "start" }}
                        onClick={() => void retryRecord(record.id)}
                      >
                        <RefreshCw size={13} />
                        重试
                      </button>
                    ) : null}
                  </div>
                );
              })}
              {records.length === 0 ? <p className="emptyText">暂无发布记录</p> : null}
            </div>

            <hr className="sectionDivider" />
            <h2 style={{ marginBottom: 12 }}>执行日志</h2>
            <div className="logList">
              {logs.map((log) => (
                <div key={log.id} className="logItem">
                  <span className={`logLevel ${log.level}`}>{log.level.toUpperCase()}</span>
                  <span style={{ flex: 1 }}>{log.message}</span>
                  <small style={{ color: "#94a3b8", flexShrink: 0 }}>
                    {new Date(log.created_at).toLocaleTimeString()}
                  </small>
                </div>
              ))}
              {logs.length === 0 ? <p className="emptyText" style={{ margin: "6px 0" }}>暂无日志</p> : null}
            </div>
          </div>
        ) : (
          <div className="taskDetail" style={{ display: "grid", placeItems: "center", minHeight: 260, color: "#94a3b8" }}>
            <p style={{ margin: 0 }}>选择左侧任务查看详情</p>
          </div>
        )}
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
