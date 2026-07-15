import { useCallback, useEffect, useMemo, useState } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import {
  AlertTriangle,
  Download,
  ExternalLink,
  Eye,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { useToast } from "../../components/Toast";
import { Modal } from "../../components/Modal";
import { ReviewBadge } from "../../components/ArticleListItem";
import { emptyDoc } from "../../api/core";
import { getArticle, listArticles } from "../../api/articles";
import { buildReadonlyExtensions } from "../content/readonlyExtensions";
import {
  adoptReference,
  getReference,
  importReference,
  listReferences,
  patchReference,
  qualityReferenceStats,
  referenceCategories,
  referenceCategoryQuestions,
  type CategoryAssoc,
  type CategoryOriginStat,
  type QualityReference,
  type QualitySimilar,
} from "../../api/qualityReference";
import type { ArticleSummary } from "../../types";

type OriginFilter = "all" | "own" | "external";
type ActiveFilter = "active" | "inactive" | "all";

const PAGE_SIZE = 20; // 偏移分页每页条数；返回 < PAGE_SIZE 即末页（后端不回 total）

function originLabel(origin: string): string {
  return origin === "external" ? "站外" : "站内";
}

// ── 多类型编辑：UI 行 <-> 后端 categories replace-all 载荷 ─────────────────────────
// 一行 = 一个问题类型 + 该类型下的问题词（单行输入，多个用逗号/顿号分隔）。
interface CatRow {
  category: string;
  questionTexts: string[]; // 该类型下已选的问题词（多选，可空）
}

// UI 行 → PATCH/import 载荷（跳过空类目；问题词空数组归一为 null）。
function rowsToCategories(rows: CatRow[]): CategoryAssoc[] {
  const out: CategoryAssoc[] = [];
  for (const r of rows) {
    const category = r.category.trim();
    if (!category) continue;
    out.push({ category, question_texts: r.questionTexts.length ? r.questionTexts : null });
  }
  return out;
}

// 后端 categories → UI 行。
function categoriesToRows(cats: CategoryAssoc[]): CatRow[] {
  return cats.map((c) => ({ category: c.category, questionTexts: c.question_texts ?? [] }));
}

// 只读展示：类型标签集（问题词挂 title 悬浮 + ·N 计数徽标）。
function CategoryTags({ items }: { items: CategoryAssoc[] }) {
  if (!items || items.length === 0) return <span style={mutedStyle}>（通用）</span>;
  return (
    <span style={{ display: "inline-flex", flexWrap: "wrap", gap: 4 }}>
      {items.map((c) => {
        const n = c.question_texts?.length ?? 0;
        return (
          <span
            key={c.category}
            className="badge"
            title={n > 0 ? (c.question_texts as string[]).join("、") : undefined}
            style={{ display: "inline-flex", alignItems: "center", gap: 3 }}
          >
            {c.category}
            {n > 0 && <span style={{ opacity: 0.6, fontSize: 10.5 }}>·{n}</span>}
          </span>
        );
      })}
    </span>
  );
}

// 可增删的多类型行编辑器（采纳 / 录入 / 详情编辑共用）。onChange 会把整份 rows 交回上层。
// 问题词：选完类型后按该类型问题池的问题词填充下拉，选中即追加标签，可空、可多选。
function CategoryRowsEditor({
  rows,
  setRows,
  categories,
  categoryQuestions,
}: {
  rows: CatRow[];
  setRows: (rows: CatRow[]) => void;
  categories: string[];
  categoryQuestions: Record<string, string[]>;
}) {
  const update = (i: number, patch: Partial<CatRow>) =>
    setRows(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));
  const remove = (i: number) => setRows(rows.filter((_, idx) => idx !== i));
  const add = () => setRows([...rows, { category: "", questionTexts: [] }]);
  // 改类型 → 清空该行已选问题词（问题词是按类型给的，跨类型残留会语义错乱）。
  const changeCategory = (i: number, category: string) => update(i, { category, questionTexts: [] });
  const addQuestion = (i: number, q: string) =>
    update(i, { questionTexts: [...rows[i].questionTexts, q] });
  const removeQuestion = (i: number, q: string) =>
    update(i, { questionTexts: rows[i].questionTexts.filter((x) => x !== q) });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {rows.length === 0 && (
        <p style={{ fontSize: 11.5, color: "var(--fg-3)", margin: 0 }}>
          未添加任何类型 = 通用兜底池（判分选参考时作通用参考）。
        </p>
      )}
      {rows.map((r, i) => {
        // 下拉候选 = 该类型问题词池减去已选；空类型时禁用下拉。
        const remaining = (categoryQuestions[r.category] ?? []).filter(
          (q) => !r.questionTexts.includes(q),
        );
        return (
          <div
            key={i}
            style={{
              display: "flex",
              flexDirection: "column",
              gap: 6,
              border: "1px solid var(--hair)",
              borderRadius: 8,
              padding: 8,
            }}
          >
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <select
                style={{ ...fieldStyle, flex: "0 0 180px", height: 34 }}
                value={r.category}
                onChange={(e) => changeCategory(i, e.target.value)}
              >
                <option value="">（选择类型）</option>
                {categories.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
                {/* 当前值不在下拉候选（历史/自由值）时补一个 option 保住选中不丢 */}
                {r.category && !categories.includes(r.category) && (
                  <option value={r.category}>{r.category}</option>
                )}
              </select>
              <select
                style={{ ...fieldStyle, flex: 1, height: 34 }}
                value=""
                disabled={!r.category}
                onChange={(e) => {
                  if (e.target.value) addQuestion(i, e.target.value);
                }}
              >
                <option value="">
                  {!r.category
                    ? "先选类型"
                    : remaining.length === 0
                      ? "（无更多问题词，可空）"
                      : "＋ 添加问题词（可空）"}
                </option>
                {remaining.map((q) => (
                  <option key={q} value={q}>
                    {q}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="secondaryButton"
                style={pillBtn}
                onClick={() => remove(i)}
                aria-label="删除该类型"
              >
                <Trash2 size={13} />
              </button>
            </div>
            {r.questionTexts.length > 0 && (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                {r.questionTexts.map((q) => (
                  <span
                    key={q}
                    className="badge"
                    style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
                  >
                    {q}
                    <button
                      type="button"
                      onClick={() => removeQuestion(i, q)}
                      aria-label={`移除问题词 ${q}`}
                      style={{
                        background: "none",
                        border: "none",
                        color: "inherit",
                        cursor: "pointer",
                        padding: 0,
                        display: "inline-flex",
                        opacity: 0.6,
                      }}
                    >
                      <X size={11} />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        );
      })}
      <button type="button" className="secondaryButton" style={{ ...pillBtn, alignSelf: "flex-start" }} onClick={add}>
        <Plus size={13} /> 添加类型
      </button>
    </div>
  );
}

// ── 只读渲染器：详情抽屉里用不可编辑的 Tiptap 渲染三份正文之一（content_json）──────────
function ReferenceReaderBody({ contentJson }: { contentJson: string }) {
  const editor = useEditor({
    editable: false,
    extensions: buildReadonlyExtensions(),
    editorProps: { attributes: { class: "editorSurface" } },
  });

  useEffect(() => {
    if (!editor) return;
    let doc: Record<string, unknown>;
    try {
      doc = JSON.parse(contentJson);
    } catch {
      doc = emptyDoc;
    }
    // 只写初始 content 不会随 contentJson 变化更新，必须显式 setContent（切换不同参考时重灌）。
    editor.commands.setContent(doc);
  }, [editor, contentJson]);

  return (
    <div className="editorWrap paper-scope">
      <EditorContent editor={editor} />
    </div>
  );
}

export function QualityReferenceWorkspace() {
  const { toast } = useToast();
  const [refs, setRefs] = useState<QualityReference[]>([]);
  const [stats, setStats] = useState<CategoryOriginStat[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [categoryQuestions, setCategoryQuestions] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(false);

  const [originFilter, setOriginFilter] = useState<OriginFilter>("all");
  const [categoryFilter, setCategoryFilter] = useState<string>("all");
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>("active");
  const [searchInput, setSearchInput] = useState(""); // 搜索框实时输入
  const [search, setSearch] = useState(""); // 已提交生效的标题关键词
  const [page, setPage] = useState(0); // 0-based 当前页

  const [detailId, setDetailId] = useState<number | null>(null);
  const [adoptOpen, setAdoptOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const q: {
        origin?: string;
        category?: string;
        is_active?: boolean;
        q?: string;
        skip?: number;
        limit?: number;
      } = { skip: page * PAGE_SIZE, limit: PAGE_SIZE };
      if (originFilter !== "all") q.origin = originFilter;
      if (categoryFilter !== "all") q.category = categoryFilter;
      if (activeFilter !== "all") q.is_active = activeFilter === "active";
      if (search) q.q = search;
      const [list, s, cats, catQs] = await Promise.all([
        listReferences(q),
        qualityReferenceStats(),
        referenceCategories(),
        referenceCategoryQuestions(),
      ]);
      setRefs(list);
      setStats(s);
      setCategories(cats);
      setCategoryQuestions(catQs);
    } catch (err) {
      toast(err instanceof Error ? err.message : "加载失败", "error");
    } finally {
      setLoading(false);
    }
  }, [originFilter, categoryFilter, activeFilter, search, page, toast]);

  useEffect(() => {
    void load();
  }, [load]);

  // 改过滤器 / 提交搜索都回到第 1 页（page 是 load 的依赖，setPage(0) 会触发重载）。
  function changeOrigin(v: OriginFilter) {
    setOriginFilter(v);
    setPage(0);
  }
  function changeCategory(v: string) {
    setCategoryFilter(v);
    setPage(0);
  }
  function changeActive(v: ActiveFilter) {
    setActiveFilter(v);
    setPage(0);
  }
  function applySearch() {
    setSearch(searchInput.trim());
    setPage(0);
  }
  function clearSearch() {
    setSearchInput("");
    setSearch("");
    setPage(0);
  }

  async function takeDown(ref: QualityReference) {
    if (!window.confirm(`下架参考「${ref.title}」？下架后不再参与对抗判分选参考。`)) return;
    try {
      await patchReference(ref.id, { is_active: false });
      toast("已下架", "success");
      await load();
    } catch (err) {
      toast(err instanceof Error ? err.message : "下架失败", "error");
    }
  }

  const emptyExternalCats = useMemo(
    () => stats.filter((s) => s.external === 0),
    [stats],
  );

  return (
    <>
      <header className="topbar">
        <div>
          <p className="eyebrow">对抗评审质量门</p>
          <h1>高质量库</h1>
        </div>
        <div className="topActions">
          <button className="secondaryButton" type="button" disabled={loading} onClick={() => void load()}>
            <RefreshCw size={15} /> 刷新
          </button>
          <button className="secondaryButton" type="button" onClick={() => setAdoptOpen(true)}>
            <Download size={15} /> 采纳站内文章
          </button>
          <button className="primaryButton" type="button" onClick={() => setImportOpen(true)}>
            <Plus size={15} /> 录入外部文章
          </button>
        </div>
      </header>

      {/* 配比告警：external / own 配比来自 stats 端点（非列表长度，列表默认只回 50 条会失真）。
          命门＝参考必须真比候选强（多为外部人写的高质量文），故某类目「external=0」标红提醒。 */}
      <div className="panel" style={{ marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
          <strong style={{ fontSize: 13 }}>各类目参考配比（仅统计启用中）</strong>
          {emptyExternalCats.length > 0 && (
            <span className="badge" style={{ color: "var(--red, #f85149)", display: "inline-flex", alignItems: "center", gap: 4 }}>
              <AlertTriangle size={12} /> {emptyExternalCats.length} 个类目无外部参考
            </span>
          )}
        </div>
        {stats.length === 0 ? (
          <p style={{ color: "var(--fg-3)", fontSize: 12, margin: 0 }}>暂无启用中的参考。</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {stats.map((s) => {
              const noExt = s.external === 0;
              return (
                <div
                  key={s.category ?? "__null__"}
                  style={{
                    border: `1px solid ${noExt ? "var(--red, #f85149)" : "var(--hair)"}`,
                    borderRadius: 10,
                    padding: "8px 12px",
                    minWidth: 150,
                    background: "var(--glass, transparent)",
                  }}
                >
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: "var(--fg)" }}>
                    {s.category ?? "（通用 / 未分类）"}
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--fg-3)", marginTop: 3 }}>
                    外部{" "}
                    <span style={{ color: noExt ? "var(--red, #f85149)" : "var(--green, #3fb950)", fontWeight: 600 }}>
                      {s.external}
                    </span>{" "}
                    / 站内 {s.own}（共 {s.total}）
                  </div>
                  {noExt && (
                    <div style={{ fontSize: 11, color: "var(--red, #f85149)", marginTop: 3 }}>
                      无外部参考，判分可能失真
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 过滤器 + 标题搜索 */}
      <div className="panel" style={{ marginBottom: 14, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <label style={filterLabel}>
          来源
          <select style={filterSelect} value={originFilter} onChange={(e) => changeOrigin(e.target.value as OriginFilter)}>
            <option value="all">全部</option>
            <option value="own">站内（own）</option>
            <option value="external">站外（external）</option>
          </select>
        </label>
        <label style={filterLabel}>
          类目
          <select style={filterSelect} value={categoryFilter} onChange={(e) => changeCategory(e.target.value)}>
            <option value="all">全部</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label style={filterLabel}>
          状态
          <select style={filterSelect} value={activeFilter} onChange={(e) => changeActive(e.target.value as ActiveFilter)}>
            <option value="active">启用中</option>
            <option value="inactive">已下架</option>
            <option value="all">全部</option>
          </select>
        </label>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input
            style={{ ...filterSelect, width: 180 }}
            value={searchInput}
            placeholder="搜索标题…"
            onChange={(e) => setSearchInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applySearch();
            }}
          />
          <button type="button" className="secondaryButton" style={pillBtn} onClick={applySearch}>
            <Search size={13} /> 搜索
          </button>
          {search && (
            <button type="button" className="secondaryButton" style={pillBtn} onClick={clearSearch}>
              清除
            </button>
          )}
        </div>
        <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--fg-3)" }}>
          第 {page + 1} 页 · 本页 {refs.length} 条{search ? `（搜索「${search}」）` : ""}
        </span>
      </div>

      {/* 列表 */}
      <div className="panel" style={{ padding: 0, overflow: "hidden" }}>
        {loading && refs.length === 0 ? (
          <p style={{ padding: 24, color: "var(--fg-3)" }}>加载中…</p>
        ) : refs.length === 0 ? (
          <p style={{ padding: 24, color: "var(--fg-3)" }}>暂无参考，试试「采纳站内文章」或「录入外部文章」。</p>
        ) : (
          <div style={{ overflow: "auto", maxHeight: "56vh" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={thStyle}>标题</th>
                  <th style={thStyle}>来源</th>
                  <th style={thStyle}>类目</th>
                  <th style={thStyle}>平台 / 链接</th>
                  <th style={thStyle}>状态</th>
                  <th style={thStyle}>创建时间</th>
                  <th style={thStyle}>操作</th>
                </tr>
              </thead>
              <tbody>
                {refs.map((r) => (
                  <tr key={r.id} style={{ borderBottom: "1px solid var(--hair)" }}>
                    <td style={tdStyle}>
                      <span style={{ fontWeight: 600 }}>{r.title}</span>
                    </td>
                    <td style={tdStyle}>
                      <span className={`badge ${r.origin === "external" ? "running" : "succeeded"}`}>
                        {originLabel(r.origin)}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <CategoryTags items={r.categories} />
                    </td>
                    <td style={tdStyle}>
                      {r.source_url ? (
                        <a
                          href={r.source_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "var(--accent, #6d6bf6)", display: "inline-flex", alignItems: "center", gap: 4 }}
                        >
                          {r.platform || "原文"} <ExternalLink size={12} />
                        </a>
                      ) : (
                        <span style={mutedStyle}>{r.platform || "—"}</span>
                      )}
                    </td>
                    <td style={tdStyle}>
                      <span className={`badge ${r.is_active ? "succeeded" : "failed"}`}>
                        {r.is_active ? "启用中" : "已下架"}
                      </span>
                    </td>
                    <td style={tdStyle}>
                      <span style={mutedStyle}>{new Date(r.created_at).toLocaleString()}</span>
                    </td>
                    <td style={tdStyle}>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button type="button" className="secondaryButton" style={pillBtn} onClick={() => setDetailId(r.id)}>
                          <Eye size={13} /> 查看
                        </button>
                        {r.is_active && (
                          <button type="button" className="secondaryButton" style={pillBtn} onClick={() => void takeDown(r)}>
                            下架
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* 翻页：无 total，返回 < PAGE_SIZE 即末页 → 下一页禁用 */}
      {(refs.length > 0 || page > 0) && (
        <div style={{ display: "flex", justifyContent: "center", alignItems: "center", gap: 12, marginTop: 12 }}>
          <button
            type="button"
            className="secondaryButton"
            style={pillBtn}
            disabled={page === 0 || loading}
            onClick={() => setPage((p) => Math.max(0, p - 1))}
          >
            上一页
          </button>
          <span style={{ fontSize: 12.5, color: "var(--fg-2)" }}>第 {page + 1} 页</span>
          <button
            type="button"
            className="secondaryButton"
            style={pillBtn}
            disabled={refs.length < PAGE_SIZE || loading}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      )}

      {detailId != null && (
        <ReferenceDetailModal
          refId={detailId}
          categories={categories}
          categoryQuestions={categoryQuestions}
          onClose={() => setDetailId(null)}
          onSaved={() => void load()}
        />
      )}
      {adoptOpen && (
        <AdoptModal
          categories={categories}
          categoryQuestions={categoryQuestions}
          onClose={() => setAdoptOpen(false)}
          onDone={() => {
            setAdoptOpen(false);
            void load();
          }}
        />
      )}
      {importOpen && (
        <ImportModal
          categories={categories}
          categoryQuestions={categoryQuestions}
          onClose={() => setImportOpen(false)}
          onDone={() => {
            setImportOpen(false);
            void load();
          }}
        />
      )}
    </>
  );
}

// ── 详情抽屉（只读 Tiptap）+ 类型关联编辑（replace-all patch）─────────────────────
function ReferenceDetailModal({
  refId,
  categories,
  categoryQuestions,
  onClose,
  onSaved,
}: {
  refId: number;
  categories: string[];
  categoryQuestions: Record<string, string[]>;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { toast } = useToast();
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof getReference>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(false);
  const [rows, setRows] = useState<CatRow[]>([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getReference(refId)
      .then((d) => {
        if (alive) setDetail(d);
      })
      .catch((err) => {
        toast(err instanceof Error ? err.message : "加载失败", "error");
        onClose();
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refId]);

  function beginEdit() {
    if (!detail) return;
    setRows(categoriesToRows(detail.categories));
    setEditing(true);
  }

  async function saveCategories() {
    setSaving(true);
    try {
      // 整体 replace-all：编辑器里的行就是最终关联（空列表 = 清空 = 通用）。
      await patchReference(refId, { categories: rowsToCategories(rows) });
      const fresh = await getReference(refId);
      setDetail(fresh);
      setEditing(false);
      toast("已更新类型关联", "success");
      onSaved();
    } catch (err) {
      toast(err instanceof Error ? err.message : "保存失败", "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      title={detail?.title ?? "参考详情"}
      onClose={onClose}
      width={860}
      maxHeight={760}
      footer={
        <button type="button" className="secondaryButton" onClick={onClose}>
          关闭
        </button>
      }
    >
      {loading || !detail ? (
        <p style={{ color: "var(--fg-3)" }}>加载中…</p>
      ) : (
        <>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12, alignItems: "center", marginBottom: 12, fontSize: 12.5 }}>
            <span className={`badge ${detail.origin === "external" ? "running" : "succeeded"}`}>{originLabel(detail.origin)}</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--fg-3)" }}>
              类目：<CategoryTags items={detail.categories} />
              {!editing && (
                <button type="button" className="secondaryButton" style={pillBtn} onClick={beginEdit}>
                  <Pencil size={12} /> 编辑
                </button>
              )}
            </span>
            {detail.origin === "external" ? (
              detail.source_url ? (
                <a
                  href={detail.source_url}
                  target="_blank"
                  rel="noreferrer"
                  style={{ color: "var(--accent, #6d6bf6)", display: "inline-flex", alignItems: "center", gap: 4 }}
                >
                  {detail.platform || "原文链接"} <ExternalLink size={12} />
                </a>
              ) : (
                <span style={{ color: "var(--fg-3)" }}>{detail.platform || "无来源链接"}</span>
              )
            ) : detail.source_article_deleted ? (
              <span style={{ color: "var(--red, #f85149)" }}>原文已删</span>
            ) : detail.article_id != null ? (
              <a
                href={`/article/${detail.article_id}`}
                style={{ color: "var(--accent, #6d6bf6)", display: "inline-flex", alignItems: "center", gap: 4 }}
              >
                查看原文 <ExternalLink size={12} />
              </a>
            ) : null}
          </div>
          {editing && (
            <div
              style={{
                border: "1px solid var(--hair)",
                borderRadius: 10,
                padding: 12,
                marginBottom: 12,
                background: "var(--surface-2, transparent)",
              }}
            >
              <div style={{ fontSize: 12.5, color: "var(--fg-2)", marginBottom: 8 }}>
                编辑问题类型关联（整体替换；清空 = 通用兜底池）：
              </div>
              <CategoryRowsEditor
                rows={rows}
                setRows={setRows}
                categories={categories}
                categoryQuestions={categoryQuestions}
              />
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button type="button" className="primaryButton" style={pillBtn} disabled={saving} onClick={() => void saveCategories()}>
                  {saving ? "保存中…" : "保存"}
                </button>
                <button type="button" className="secondaryButton" style={pillBtn} disabled={saving} onClick={() => setEditing(false)}>
                  取消
                </button>
              </div>
            </div>
          )}
          <ReferenceReaderBody contentJson={detail.content_json} />
        </>
      )}
    </Modal>
  );
}

// ── 采纳站内文章 ──────────────────────────────────────────────────────────────
function AdoptModal({
  categories,
  categoryQuestions,
  onClose,
  onDone,
}: {
  categories: string[];
  categoryQuestions: Record<string, string[]>;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useToast();
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<ArticleSummary[]>([]);
  const [searched, setSearched] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  // 采纳前的类型关联编辑：预填文章溯源类目（有则一行），可加多条；edited 决定是否发 replace-all patch。
  const [pick, setPick] = useState<{
    articleId: number;
    title: string;
    rows: CatRow[];
    edited: boolean;
  } | null>(null);

  async function runSearch() {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearched(true);
    try {
      if (/^\d+$/.test(q)) {
        const a = await getArticle(Number(q)); // 纯数字直接按 ID 取（不能塞进 FTS 的 q）
        setResults([a]);
      } else {
        const list = await listArticles(new URLSearchParams({ q, review_status: "approved" }));
        setResults(list);
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : "搜索失败", "error");
      setResults([]);
    } finally {
      setSearching(false);
    }
  }

  async function beginAdopt(articleId: number) {
    setBusyId(articleId);
    try {
      const full = await getArticle(articleId); // 采纳前取详情读溯源类目 / 问题词预填编辑器
      const cat = full.source_question_category?.trim();
      const initialRows: CatRow[] = cat
        ? [{ category: cat, questionTexts: full.source_question_texts ?? [] }]
        : [];
      setPick({ articleId, title: full.title, rows: initialRows, edited: false });
    } catch (err) {
      toast(err instanceof Error ? err.message : "采纳失败", "error");
    } finally {
      setBusyId(null);
    }
  }

  async function confirmAdopt() {
    if (!pick) return;
    const { articleId, rows, edited } = pick;
    const cats = rowsToCategories(rows);
    // 单值 fallback 交后端在「新插入且无溯源类目」时兜底关联；有溯源类目时后端忽略它。
    const fallback = cats[0]?.category ?? null;
    setPick(null);
    try {
      const ref = await adoptReference({ article_id: articleId, category: fallback });
      // 仅当用户动过编辑器才 replace-all：未动 = 尊重后端自动关联（含 dup 复活不动关联）。
      if (edited) await patchReference(ref.id, { categories: cats });
      toast("已采纳到高质量库", "success");
      onDone();
    } catch (err) {
      toast(err instanceof Error ? err.message : "采纳失败", "error");
    }
  }

  return (
    <Modal
      title="采纳站内文章"
      onClose={onClose}
      width={640}
      maxHeight={640}
      footer={
        <button type="button" className="secondaryButton" onClick={onClose}>
          关闭
        </button>
      }
    >
      <p style={{ fontSize: 12, color: "var(--fg-3)", marginTop: 0 }}>
        仅能采纳「已审核（approved）」文章。输入纯数字按文章 ID 精确取；否则按关键词搜索（命中标题 / 作者 / 正文）。
      </p>
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        <input
          style={{ ...fieldStyle, flex: 1 }}
          value={query}
          placeholder="文章 ID 或关键词"
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") void runSearch();
          }}
        />
        <button type="button" className="primaryButton" disabled={searching} onClick={() => void runSearch()}>
          <Search size={14} /> 搜索
        </button>
      </div>

      {pick && (
        <div
          style={{
            border: "1px solid var(--hair)",
            borderRadius: 10,
            padding: 12,
            marginBottom: 12,
            background: "var(--surface-2, transparent)",
          }}
        >
          <div style={{ fontSize: 12.5, color: "var(--fg)", marginBottom: 8 }}>
            为「{pick.title}」设置问题类型关联（可加多条；不填 = 通用兜底池）：
          </div>
          <CategoryRowsEditor
            rows={pick.rows}
            setRows={(rows) => setPick({ ...pick, rows, edited: true })}
            categories={categories}
            categoryQuestions={categoryQuestions}
          />
          <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
            <button type="button" className="primaryButton" style={pillBtn} onClick={() => void confirmAdopt()}>
              确认采纳
            </button>
            <button type="button" className="secondaryButton" style={pillBtn} onClick={() => setPick(null)}>
              取消
            </button>
          </div>
        </div>
      )}

      {searching ? (
        <p style={{ color: "var(--fg-3)", fontSize: 12 }}>搜索中…</p>
      ) : searched && results.length === 0 ? (
        <p style={{ color: "var(--fg-3)", fontSize: 12 }}>无匹配文章。</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {results.map((a) => (
            <div
              key={a.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                border: "1px solid var(--hair)",
                borderRadius: 10,
                padding: "8px 12px",
              }}
            >
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontWeight: 600, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                  {a.title}
                </div>
                <div style={{ fontSize: 11.5, color: "var(--fg-3)", display: "flex", alignItems: "center", gap: 6 }}>
                  ID {a.id} <ReviewBadge status={a.review_status} />
                </div>
              </div>
              <button
                type="button"
                className="primaryButton"
                style={pillBtn}
                disabled={busyId === a.id}
                onClick={() => void beginAdopt(a.id)}
              >
                {busyId === a.id ? "…" : "采纳"}
              </button>
            </div>
          ))}
        </div>
      )}
    </Modal>
  );
}

// ── 录入外部文章 ──────────────────────────────────────────────────────────────
function ImportModal({
  categories,
  categoryQuestions,
  onClose,
  onDone,
}: {
  categories: string[];
  categoryQuestions: Record<string, string[]>;
  onClose: () => void;
  onDone: () => void;
}) {
  const { toast } = useToast();
  const [title, setTitle] = useState("");
  const [catRows, setCatRows] = useState<CatRow[]>([]);
  const [markdown, setMarkdown] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [platform, setPlatform] = useState("");
  const [saving, setSaving] = useState(false);
  // 提交后若查重命中，展示疑似重复列表（不阻断——参考已入库），用户确认后关闭。
  const [similar, setSimilar] = useState<QualitySimilar[] | null>(null);

  async function submit() {
    if (!title.trim()) {
      toast("标题不能为空", "error");
      return;
    }
    if (!markdown.trim()) {
      toast("正文不能为空", "error");
      return;
    }
    setSaving(true);
    try {
      const cats = rowsToCategories(catRows);
      // 首个类目走 import 参数；多于一条时再 replace-all patch 补齐全部（plan §22）。
      const res = await importReference({
        title: title.trim(),
        markdown,
        category: cats[0]?.category ?? null,
        question_texts: cats[0]?.question_texts ?? null,
        source_url: sourceUrl.trim() || null,
        platform: platform.trim() || null,
      });
      if (cats.length > 1) {
        await patchReference(res.reference.id, { categories: cats });
      }
      if (res.similar.length > 0) {
        setSimilar(res.similar); // 已入库，仅软提示
      } else {
        toast("已录入高质量库", "success");
        onDone();
      }
    } catch (err) {
      toast(err instanceof Error ? err.message : "录入失败", "error");
    } finally {
      setSaving(false);
    }
  }

  if (similar) {
    return (
      <Modal
        title="已录入（发现疑似重复）"
        onClose={onDone}
        width={520}
        footer={
          <button type="button" className="primaryButton" onClick={onDone}>
            知道了
          </button>
        }
      >
        <p style={{ fontSize: 12.5, color: "var(--fg-2)", marginTop: 0 }}>
          参考已录入。以下是库中疑似重复的条目（仅提示，不影响本次录入）：
        </p>
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
          {similar.map((s) => (
            <li key={s.id} style={{ marginBottom: 4 }}>
              {s.title}（ID {s.id}）
            </li>
          ))}
        </ul>
      </Modal>
    );
  }

  return (
    <Modal
      title="录入外部文章"
      onClose={onClose}
      width={640}
      maxHeight={720}
      footer={
        <>
          <button type="button" className="secondaryButton" onClick={onClose} disabled={saving}>
            取消
          </button>
          <button type="button" className="primaryButton" onClick={() => void submit()} disabled={saving}>
            {saving ? "录入中…" : "录入"}
          </button>
        </>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <label style={fieldColumn}>
          <span style={fieldLabelText}>标题</span>
          <input style={fieldStyle} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="外部文章标题" />
        </label>
        <div style={fieldColumn}>
          <span style={fieldLabelText}>问题类型关联（可加多条；不填 = 通用兜底池）</span>
          <CategoryRowsEditor
            rows={catRows}
            setRows={setCatRows}
            categories={categories}
            categoryQuestions={categoryQuestions}
          />
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <label style={{ ...fieldColumn, flex: 1 }}>
            <span style={fieldLabelText}>来源链接（可空）</span>
            <input style={fieldStyle} value={sourceUrl} onChange={(e) => setSourceUrl(e.target.value)} placeholder="https://…" />
          </label>
          <label style={{ ...fieldColumn, flex: 1 }}>
            <span style={fieldLabelText}>平台（可空）</span>
            <input style={fieldStyle} value={platform} onChange={(e) => setPlatform(e.target.value)} placeholder="如 知乎 / 公众号" />
          </label>
        </div>
        <label style={fieldColumn}>
          <span style={fieldLabelText}>正文（Markdown）</span>
          <textarea
            style={{ ...fieldStyle, height: 240, padding: 10, resize: "vertical", lineHeight: 1.6 }}
            value={markdown}
            onChange={(e) => setMarkdown(e.target.value)}
            placeholder="粘贴 Markdown 正文…"
          />
        </label>
      </div>
    </Modal>
  );
}

const thStyle: React.CSSProperties = {
  padding: "10px 16px",
  textAlign: "left",
  fontWeight: 600,
  color: "var(--fg-3)",
  fontSize: 12,
  whiteSpace: "nowrap",
  position: "sticky",
  top: 0,
  zIndex: 1,
  background: "var(--surface-2)",
  boxShadow: "inset 0 -1px 0 var(--hair)",
};

const tdStyle: React.CSSProperties = { padding: "10px 16px", verticalAlign: "middle" };
const mutedStyle: React.CSSProperties = { color: "var(--fg-3)" };
const pillBtn: React.CSSProperties = { padding: "4px 10px", fontSize: 12 };

const filterLabel: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: 12.5,
  color: "var(--fg-2)",
};

const filterSelect: React.CSSProperties = {
  height: 32,
  padding: "0 8px",
  border: "1px solid var(--hair)",
  borderRadius: 8,
  background: "var(--paper, var(--glass))",
  color: "var(--fg)",
  fontSize: 12.5,
  colorScheme: "dark",
};

const fieldStyle: React.CSSProperties = {
  width: "100%",
  height: 38,
  padding: "0 12px",
  border: "1px solid var(--hair-2, var(--hair))",
  borderRadius: 10,
  background: "var(--paper, var(--glass))",
  color: "var(--fg)",
  fontSize: 13,
  colorScheme: "dark",
  boxSizing: "border-box",
};

const fieldColumn: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 6 };
const fieldLabelText: React.CSSProperties = { fontSize: 11.5, fontWeight: 500, color: "var(--fg-2)" };
