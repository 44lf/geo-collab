import { Suspense, useState, type ComponentType } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { navItems, AUDIT_LOG_CHILDREN } from "./types";
import type { NavKey, NavChild } from "./types";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider } from "./components/Toast";
import { GlobalErrorListener } from "./components/GlobalErrorListener";
import { useAuth } from "./features/auth/AuthContext";
import { LoginPage } from "./features/auth/LoginPage";
import { ChangePasswordPage } from "./features/auth/ChangePasswordPage";
import { ChevronDown, ChevronLeft, Cpu, LogOut, ScrollText, ServerCog, Users } from "lucide-react";
import { MobileNav } from "./components/MobileNav";
import { MobileMorePage } from "./components/MobileMorePage";
import { useIsMobile } from "./hooks/useIsMobile";
import "./styles.css";

// 所有合法的顶级导航 key（= URL 首段）。
const KNOWN_NAV: NavKey[] = [
  "agents",
  "content",
  "prompts",
  "quality-reference",
  "game-library",
  "videos",
  "media",
  "tasks",
  "system",
  "mcp-connect",
  "admin",
  "audit-logs",
  "ai-models",
  "collector-management",
];

// 每个 tab 的标题，用于 ErrorBoundary。
const TAB_TITLES: Record<NavKey, string> = {
  agents: "智能体管理",
  content: "内容管理",
  prompts: "提示词管理",
  "quality-reference": "高质量库",
  "game-library": "游戏库",
  videos: "视频库",
  media: "媒体矩阵",
  tasks: "分发引擎",
  system: "系统状态",
  "mcp-connect": "MCP 接入",
  admin: "用户管理",
  "audit-logs": "日志中心",
  "ai-models": "AI 模型管理",
  "collector-management": "Collector 管理",
};

// 移动端底栏 4 个高频入口；其余归「更多」分区。
const BOTTOM_KEYS: NavKey[] = ["agents", "content", "tasks"];

function pathToNavKey(pathname: string): NavKey {
  const seg = pathname.split("/").filter(Boolean)[0];
  if (seg === "article") return "content"; // 永久链接 /article/:id 归入「内容管理」高亮
  return (KNOWN_NAV as string[]).includes(seg) ? (seg as NavKey) : "agents";
}

function subSegment(pathname: string): string {
  return pathname.split("/").filter(Boolean)[1] ?? "";
}

function TabFallback() {
  return (
    <p className="emptyText" style={{ padding: 24 }}>
      页面加载中...
    </p>
  );
}

function NavGroup({
  navKey,
  label,
  icon: Icon,
  children,
  activeNav,
  isOpen,
  childValue,
  onParentClick,
  onToggle,
  onSelectChild,
}: {
  navKey: NavKey;
  label: string;
  icon: ComponentType<{ size?: number }>;
  children: NavChild[];
  activeNav: NavKey;
  isOpen: boolean;
  childValue: string;
  onParentClick: () => void;
  onToggle: () => void;
  onSelectChild: (value: string) => void;
}) {
  return (
    <div className="navGroup">
      <button
        className={`navItem navParent ${activeNav === navKey ? "active" : ""}`}
        type="button"
        onClick={() => {
          if (activeNav === navKey) onToggle();
          else onParentClick();
        }}
      >
        <Icon size={17} />
        <span>{label}</span>
        <ChevronDown size={15} className={`navChevron${isOpen ? " open" : ""}`} />
      </button>
      <div className={`navSub ${isOpen ? "open" : ""}`}>
        <div className="navChildren">
          <div className="navChildrenInner">
            {children.map((child) => {
              const childActive = activeNav === navKey && childValue === child.value;
              return (
                <button
                  className={`navChild ${childActive ? "active" : ""}`}
                  key={child.key}
                  type="button"
                  onClick={() => onSelectChild(child.value)}
                >
                  {child.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </div>
  );
}

export function RootLayout() {
  const { user, loading, logout } = useAuth();
  const isMobile = useIsMobile();
  const location = useLocation();
  const navigate = useNavigate();
  const activeNav = pathToNavKey(location.pathname);
  const onMoreSection = !BOTTOM_KEYS.includes(activeNav);
  const [moreOpen, setMoreOpen] = useState(false);
  // 侧栏分组展开态；初始把当前所在的有子项分组（内容/提示词）展开。
  const [openGroup, setOpenGroup] = useState<NavKey | null>(() => {
    const k = pathToNavKey(location.pathname);
    const inNavItems = navItems.some((i) => i.key === k && (i.children?.length ?? 0) > 0);
    if (inNavItems || k === "audit-logs") return k;
    return null;
  });

  function go(key: NavKey) {
    setMoreOpen(false);
    navigate(`/${key}`);
    const hasChildren = navItems.some((i) => i.key === key && (i.children?.length ?? 0) > 0);
    if (!hasChildren) setOpenGroup(null);
  }

  function toggleGroup(key: NavKey) {
    setOpenGroup((prev) => (prev === key ? null : key));
  }

  // 当前激活 tab 的子页选中值（从 URL 子段派生）。
  function childValueFor(parentKey: NavKey): string {
    if (parentKey !== activeNav) return "";
    const seg = subSegment(location.pathname);
    if (parentKey === "content") return seg || "pending";
    if (parentKey === "prompts") return seg || "generation";
    if (parentKey === "audit-logs") return seg || "audit";
    return "";
  }

  function selectChild(parentKey: NavKey, value: string) {
    setOpenGroup(parentKey);
    setMoreOpen(false);
    navigate(`/${parentKey}/${value}`);
  }

  if (loading) {
    return (
      <div className="authShell">
        <div className="authCard">
          <p className="authLoading">加载中...</p>
        </div>
      </div>
    );
  }
  if (!user) return <LoginPage />;
  if (user.must_change_password) return <ChangePasswordPage />;

  return (
    <ToastProvider>
      {/* 必须挂在 ToastProvider 内（useToast 的 context 默认值是 no-op）；登录页无 toast 面板，自带内联报错 */}
      <GlobalErrorListener />
      <main className={`shell${isMobile ? " shellMobile" : ""}`}>
        {isMobile && (
          <header className="mobileAppBar">
            {onMoreSection ? (
              <button type="button" className="mobileAppBack" onClick={() => setMoreOpen(true)}>
                <ChevronLeft size={22} />
                <span>更多</span>
              </button>
            ) : (
              <>
                <div className="brandMark">AI</div>
                <span className="mobileAppName">AI智能体平台</span>
              </>
            )}
          </header>
        )}
        <aside className="sidebar">
          <div className="brand">
            <div className="brandMark">AI</div>
            <span className="brandName">AI智能体平台</span>
          </div>
          <nav className="nav">
            {navItems.map((item) => {
              const Icon = item.icon;
              if (item.children && item.children.length > 0) {
                return (
                  <NavGroup
                    key={item.key}
                    navKey={item.key}
                    label={item.label}
                    icon={Icon}
                    children={item.children}
                    activeNav={activeNav}
                    isOpen={openGroup === item.key}
                    childValue={childValueFor(item.key)}
                    onParentClick={() => {
                      go(item.key);
                      setOpenGroup(item.key);
                    }}
                    onToggle={() => toggleGroup(item.key)}
                    onSelectChild={(value) => selectChild(item.key, value)}
                  />
                );
              }
              return (
                <button
                  className={`navItem ${activeNav === item.key ? "active" : ""}`}
                  key={item.key}
                  type="button"
                  onClick={() => go(item.key)}
                >
                  <Icon size={17} />
                  <span>{item.label}</span>
                  <span className="navDot" />
                </button>
              );
            })}
            {user.role === "admin" && (
              <button
                className={`navItem ${activeNav === "collector-management" ? "active" : ""}`}
                type="button"
                onClick={() => go("collector-management")}
              >
                <ServerCog size={17} />
                <span>Collector 管理</span>
                <span className="navDot" />
              </button>
            )}
            {user.role === "admin" && (
              <button
                className={`navItem ${activeNav === "admin" ? "active" : ""}`}
                type="button"
                onClick={() => go("admin")}
              >
                <Users size={17} />
                <span>用户管理</span>
                <span className="navDot" />
              </button>
            )}
            {user.role === "admin" && (
              <NavGroup
                navKey="audit-logs"
                label="日志中心"
                icon={ScrollText}
                children={AUDIT_LOG_CHILDREN}
                activeNav={activeNav}
                isOpen={openGroup === "audit-logs"}
                childValue={childValueFor("audit-logs")}
                onParentClick={() => {
                  go("audit-logs");
                  setOpenGroup("audit-logs");
                }}
                onToggle={() => toggleGroup("audit-logs")}
                onSelectChild={(value) => selectChild("audit-logs", value)}
              />
            )}
            {user.role === "admin" && (
              <button
                className={`navItem ${activeNav === "ai-models" ? "active" : ""}`}
                type="button"
                onClick={() => go("ai-models")}
              >
                <Cpu size={17} />
                <span>AI 模型</span>
                <span className="navDot" />
              </button>
            )}
          </nav>
          <div className="sidebarUser">
            <span className="sidebarUsername">{user.username}</span>
            <button className="sidebarLogoutBtn" type="button" onClick={logout} title="退出登录">
              <LogOut size={15} />
            </button>
          </div>
        </aside>
        <section className="workspace">
          <div className="workspaceInner">
            {/* `.workspaceInner > div` 是滚动容器（overflow-y:auto，见 styles.css）；
                route-per-page 下只有一个直接子 div 承接它，ErrorBoundary 随 tab 切换重置（key=activeNav）。 */}
            <div className="workspaceScroll">
              <ErrorBoundary key={activeNav} title={TAB_TITLES[activeNav]}>
                <Suspense fallback={<TabFallback />}>
                  <Outlet />
                </Suspense>
              </ErrorBoundary>
            </div>
          </div>
        </section>
        {isMobile && moreOpen && (
          <MobileMorePage
            username={user.username}
            role={user.role}
            isAdmin={user.role === "admin"}
            onNavigate={(key) => go(key)}
            onLogout={() => {
              if (window.confirm("确定退出登录？")) logout();
            }}
          />
        )}
        {isMobile && (
          <MobileNav
            activeNav={activeNav}
            onNavigate={(key) => go(key)}
            moreActive={moreOpen || onMoreSection}
            onMoreClick={() => setMoreOpen((v) => !v)}
          />
        )}
      </main>
    </ToastProvider>
  );
}
