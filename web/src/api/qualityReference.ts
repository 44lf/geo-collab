import { api } from "./core";

// 高质量库（对抗评审质量门）—— 采纳站内文章 / 录入站外参考 / 列表 / 详情 / 下架 / 配比聚合。
// 全部走 user JWT cookie（后端 quality_reference_router 挂在 /api/quality-reference）。

export interface QualityReference {
  id: number;
  origin: string; // "own" | "external"
  article_id: number | null;
  title: string;
  category: string | null;
  source_url: string | null;
  platform: string | null;
  is_active: boolean;
  created_at: string;
}

export interface QualityReferenceDetail extends QualityReference {
  content_json: string; // 序列化后的 Tiptap 文档（只读渲染时 JSON.parse）
  content_html: string;
  plain_text: string;
  source_article_deleted: boolean; // own 参考源文章已软删/物理删时为真 → 提示「原文已删」
}

export interface QualitySimilar {
  id: number;
  title: string;
}

export interface CategoryOriginStat {
  category: string | null;
  external: number;
  own: number;
  total: number;
}

export const adoptReference = (b: { article_id: number; category?: string | null }) =>
  api<QualityReference>("/api/quality-reference/adopt", { method: "POST", body: JSON.stringify(b) });

export const importReference = (b: {
  title: string;
  markdown: string;
  category?: string | null;
  source_url?: string | null;
  platform?: string | null;
}) =>
  api<{ reference: QualityReference; similar: QualitySimilar[] }>("/api/quality-reference/import", {
    method: "POST",
    body: JSON.stringify(b),
  });

export const listReferences = (q: { origin?: string; category?: string; is_active?: boolean }) => {
  const s = new URLSearchParams(
    Object.entries(q)
      .filter(([, v]) => v != null && v !== "")
      .map(([k, v]) => [k, String(v)]),
  );
  const qs = s.toString();
  return api<QualityReference[]>(qs ? `/api/quality-reference?${qs}` : "/api/quality-reference");
};

export const getReference = (id: number) =>
  api<QualityReferenceDetail>(`/api/quality-reference/${id}`);

export const patchReference = (id: number, b: { is_active?: boolean; category?: string }) =>
  api<QualityReference>(`/api/quality-reference/${id}`, {
    method: "PATCH",
    body: JSON.stringify(b),
  });

export const referenceCategories = () => api<string[]>("/api/quality-reference/categories");

// 配比告警：按类目聚合 external/own 计数（不用列表数组长度算——列表默认只回 50 条会失真）。
export const qualityReferenceStats = () =>
  api<CategoryOriginStat[]>("/api/quality-reference/stats");
