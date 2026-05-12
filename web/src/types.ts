import { FileText, MonitorCog, RadioTower, Send } from "lucide-react";
import type { ComponentType } from "react";

export type NavKey = "content" | "media" | "tasks" | "system";

export type Asset = {
  id: string;
  filename: string;
  mime_type: string;
  size: number;
  width: number | null;
  height: number | null;
  url: string;
};

export type ArticleBodyAsset = {
  asset_id: string;
  position: number;
  editor_node_id: string | null;
};

export type ArticleSummary = {
  id: number;
  title: string;
  author: string | null;
  cover_asset_id: string | null;
  word_count: number;
  status: string;
  version: number;
  published_count: number;
  created_at: string;
  updated_at: string;
};

export type Article = ArticleSummary & {
  content_json: Record<string, unknown>;
  content_html: string;
  plain_text: string;
  body_assets: ArticleBodyAsset[];
};

export type ArticleGroup = {
  id: number;
  name: string;
  items: { article_id: number; sort_order: number }[];
  version: number;
  created_at: string;
  updated_at: string;
};

export type Account = {
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

export type Draft = {
  id: number | null;
  title: string;
  author: string;
  cover_asset_id: string | null;
  status: string;
  version: number | null;
};

export type TaskAccountRead = {
  account_id: number;
  sort_order: number;
  display_name: string;
  status: string;
};

export type Task = {
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

export type PublishRecord = {
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
  remote_browser_session_id: string | null;
  novnc_url: string | null;
};

export type TaskLog = {
  id: number;
  task_id: number;
  record_id: number | null;
  level: string;
  message: string;
  screenshot_asset_id: string | null;
  created_at: string;
};

export type AssignmentPreview = {
  task_type: string;
  platform_code: string;
  article_count: number;
  account_count: number;
  items: { position: number; article_id: number; account_id: number; account_sort_order: number }[];
};

export type SystemStatus = {
  service: string;
  directories_ready: boolean;
  article_count: number;
  account_count: number;
  task_count: number;
  browser_ready: boolean;
};

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "待执行",
    running: "执行中",
    succeeded: "成功",
    partial_failed: "部分失败",
    failed: "失败",
    cancelled: "已取消",
    waiting_manual_publish: "等待确认",
    waiting_user_input: "需要处理",
  };
  return labels[status] ?? status;
}

export const navItems: { key: NavKey; label: string; icon: ComponentType<{ size?: number }> }[] = [
  { key: "content", label: "内容管理", icon: FileText },
  { key: "media", label: "媒体矩阵", icon: RadioTower },
  { key: "tasks", label: "分发引擎", icon: Send },
  { key: "system", label: "系统状态", icon: MonitorCog },
];

export const TERMINAL_STATUSES = new Set(["succeeded", "partial_failed", "failed", "cancelled"]);

export const ITEM_HEIGHT = 82;
