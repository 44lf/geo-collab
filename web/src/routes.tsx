/* eslint-disable react-refresh/only-export-components -- 路由配置文件：导出 router 之外的组件定义不参与 Fast Refresh，符合预期 */
import { lazy } from "react";
import { createBrowserRouter, Navigate, useNavigate, useParams } from "react-router-dom";
import type { ReactElement } from "react";
import { RootLayout } from "./App";
import { useAuth } from "./features/auth/AuthContext";
import { useIsMobile } from "./hooks/useIsMobile";
import type { PromptScope, ReviewStatus } from "./types";

// 各工作区仍走代码分割懒加载（动态 import → 独立 chunk）；
// RootLayout 的 <Outlet/> 外层有统一 <Suspense> 兜住加载态。
const AgentManagementWorkspace = lazy(() =>
  import("./features/pipelines/AgentManagementWorkspace").then((m) => ({ default: m.AgentManagementWorkspace })),
);
const AiGenerationWorkspace = lazy(() =>
  import("./features/ai-generation/AiGenerationWorkspace").then((m) => ({ default: m.AiGenerationWorkspace })),
);
const ImageLibraryWorkspace = lazy(() =>
  import("./features/image-library/ImageLibraryWorkspace").then((m) => ({ default: m.ImageLibraryWorkspace })),
);
const ContentWorkspace = lazy(() =>
  import("./features/content/ContentWorkspace").then((m) => ({ default: m.ContentWorkspace })),
);
const PromptsWorkspace = lazy(() =>
  import("./features/prompt-templates/PromptsWorkspace").then((m) => ({ default: m.PromptsWorkspace })),
);
const AccountsWorkspace = lazy(() =>
  import("./features/accounts/AccountsWorkspace").then((m) => ({ default: m.AccountsWorkspace })),
);
const TasksWorkspace = lazy(() =>
  import("./features/tasks/TasksWorkspace").then((m) => ({ default: m.TasksWorkspace })),
);
const SystemWorkspace = lazy(() =>
  import("./features/system/SystemWorkspace").then((m) => ({ default: m.SystemWorkspace })),
);
const McpConnectWorkspace = lazy(() =>
  import("./features/mcp/McpConnectWorkspace").then((m) => ({ default: m.McpConnectWorkspace })),
);
const UsersWorkspace = lazy(() =>
  import("./features/auth/UsersWorkspace").then((m) => ({ default: m.UsersWorkspace })),
);
const AuditLogsWorkspace = lazy(() =>
  import("./features/system/AuditLogsWorkspace").then((m) => ({ default: m.AuditLogsWorkspace })),
);
const AiModelsWorkspace = lazy(() =>
  import("./features/system/AiModelsWorkspace").then((m) => ({ default: m.AiModelsWorkspace })),
);
const VideosWorkspace = lazy(() =>
  import("./features/videos/VideosWorkspace").then((m) => ({ default: m.VideosWorkspace })),
);
const QualityReferenceWorkspace = lazy(() =>
  import("./features/quality-reference/QualityReferenceWorkspace").then((m) => ({
    default: m.QualityReferenceWorkspace,
  })),
);
const XhsStyleGallery = lazy(() =>
  import("./features/prompt-templates/XhsStyleGallery").then((m) => ({ default: m.XhsStyleGallery })),
);

// admin 专属页守卫：非 admin 直接重定向回默认页（RootLayout 已保证此处必有登录用户）。
function RequireAdmin({ children }: { children: ReactElement }) {
  const { user } = useAuth();
  if (user?.role !== "admin") return <Navigate to="/agents" replace />;
  return children;
}

// 「内容管理」子页（未审核 / 已审核）由 URL 段驱动：/content/:status。
// 同一组件也承接永久链接 /article/:articleId —— 复用同一元素让 React reconcile 而非重挂，
// 保住编辑器草稿 / savedStateRef（详见设计 §3.2）。
function ContentRoute() {
  const { status, articleId } = useParams();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const reviewTab: ReviewStatus = status === "approved" ? "approved" : "pending";
  // 永久链接非法（非数字 / 0 / 负 / 非整数）→ 回落内容管理，避免静默空白。
  // 合法但不存在的 id 交给 ContentWorkspace 内 getArticle 走 404 toast。
  const parsedId = articleId !== undefined ? Number(articleId) : undefined;
  if (articleId !== undefined && (parsedId === undefined || !Number.isInteger(parsedId) || parsedId <= 0)) {
    return <Navigate to="/content" replace />;
  }
  return (
    <ContentWorkspace
      isActive
      reviewTab={reviewTab}
      isMobile={isMobile}
      deepLinkArticleId={parsedId}
      onReviewTabChange={(t) => navigate(`/content/${t}`)}
    />
  );
}

const PROMPT_SCOPES: PromptScope[] = ["generation", "ai_format", "image_search", "image_companion"];

// 「提示词管理」子页（4 个 scope）由 URL 段驱动：/prompts/:scope。
function PromptsRoute() {
  const { scope } = useParams();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  if (scope === "xhs_styles") return <XhsStyleGallery />;
  const active: PromptScope = PROMPT_SCOPES.includes(scope as PromptScope)
    ? (scope as PromptScope)
    : "generation";
  return (
    <PromptsWorkspace
      scope={active}
      isMobile={isMobile}
      onScopeChange={(s) => navigate(`/prompts/${s}`)}
    />
  );
}

// 「日志中心」子页（审计日志 / 打点日志）由 URL 段驱动：/audit-logs/:tab。
function AuditLogsRoute() {
  const { tab } = useParams();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const active: "audit" | "events" = tab === "events" ? "events" : "audit";
  return (
    <AuditLogsWorkspace
      tab={active}
      isMobile={isMobile}
      onTabChange={(t) => navigate(`/audit-logs/${t}`)}
    />
  );
}

// AI 生文里「打开文章」跳转到内容管理。
function AiRoute() {
  const navigate = useNavigate();
  return <AiGenerationWorkspace onNavigateToContent={() => navigate("/content")} />;
}

export const router = createBrowserRouter([
  {
    path: "/",
    element: <RootLayout />,
    children: [
      { index: true, element: <Navigate to="/agents" replace /> },
      { path: "agents", element: <AgentManagementWorkspace /> },
      { path: "ai", element: <AiRoute /> },
      { path: "content", element: <ContentRoute /> },
      { path: "content/:status", element: <ContentRoute /> },
      { path: "article/:articleId", element: <ContentRoute /> },
      { path: "prompts", element: <PromptsRoute /> },
      { path: "prompts/:scope", element: <PromptsRoute /> },
      { path: "quality-reference", element: <QualityReferenceWorkspace /> },
      { path: "image-library", element: <ImageLibraryWorkspace /> },
      { path: "videos", element: <VideosWorkspace /> },
      { path: "media", element: <AccountsWorkspace isActive /> },
      { path: "tasks", element: <TasksWorkspace isActive /> },
      { path: "system", element: <SystemWorkspace /> },
      { path: "mcp-connect", element: <McpConnectWorkspace /> },
      {
        path: "admin",
        element: (
          <RequireAdmin>
            <UsersWorkspace />
          </RequireAdmin>
        ),
      },
      {
        path: "audit-logs",
        element: (
          <RequireAdmin>
            <AuditLogsRoute />
          </RequireAdmin>
        ),
      },
      {
        path: "audit-logs/:tab",
        element: (
          <RequireAdmin>
            <AuditLogsRoute />
          </RequireAdmin>
        ),
      },
      {
        path: "ai-models",
        element: (
          <RequireAdmin>
            <AiModelsWorkspace />
          </RequireAdmin>
        ),
      },
      { path: "*", element: <Navigate to="/agents" replace /> },
    ],
  },
]);
