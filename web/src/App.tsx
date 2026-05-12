import { useRef, useState } from "react";
import { navItems } from "./types";
import type { NavKey } from "./types";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ToastProvider } from "./components/Toast";
import { ContentWorkspace } from "./features/content/ContentWorkspace";
import { AccountsWorkspace } from "./features/accounts/AccountsWorkspace";
import { TasksWorkspace } from "./features/tasks/TasksWorkspace";
import { SystemWorkspace } from "./features/system/SystemWorkspace";
import "./styles.css";

export default function App() {
  const [activeNav, setActiveNav] = useState<NavKey>("content");
  const contentDirtyRef = useRef<() => boolean>(() => false);

  function handleNavClick(key: NavKey) {
    if (activeNav === "content" && key !== "content" && contentDirtyRef.current()) {
      if (!window.confirm("当前文章有未保存内容，确定要切换页面吗？未保存的修改将丢失。")) return;
    }
    setActiveNav(key);
  }

  return (
    <ToastProvider>
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
                  onClick={() => handleNavClick(item.key)}
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
          <div className="workspaceInner">
            <div style={{ display: activeNav === "content" ? undefined : "none" }}>
              <ErrorBoundary fallback={<p role="alert">内容管理出错，请刷新重试</p>}>
                <ContentWorkspace dirtyCheckRef={contentDirtyRef} />
              </ErrorBoundary>
            </div>
            <div style={{ display: activeNav === "media" ? undefined : "none" }}>
              <ErrorBoundary fallback={<p role="alert">媒体矩阵出错，请刷新重试</p>}>
                <AccountsWorkspace />
              </ErrorBoundary>
            </div>
            <div style={{ display: activeNav === "tasks" ? undefined : "none" }}>
              <ErrorBoundary fallback={<p role="alert">分发引擎出错，请刷新重试</p>}>
                <TasksWorkspace />
              </ErrorBoundary>
            </div>
            <div style={{ display: activeNav === "system" ? undefined : "none" }}>
              <ErrorBoundary fallback={<p role="alert">系统状态出错，请刷新重试</p>}>
                <SystemWorkspace />
              </ErrorBoundary>
            </div>
          </div>
        </section>
      </main>
    </ToastProvider>
  );
}
