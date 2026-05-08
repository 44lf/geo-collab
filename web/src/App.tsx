import { useState } from "react";
import { navItems } from "./types";
import type { NavKey } from "./types";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { ContentWorkspace } from "./features/content/ContentWorkspace";
import { AccountsWorkspace } from "./features/accounts/AccountsWorkspace";
import { TasksWorkspace } from "./features/tasks/TasksWorkspace";
import { SystemWorkspace } from "./features/system/SystemWorkspace";
import "./styles.css";

export default function App() {
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
          {activeNav === "content" && (
            <ErrorBoundary fallback={<p role="alert">内容管理出错，请刷新重试</p>}>
              <ContentWorkspace />
            </ErrorBoundary>
          )}
          {activeNav === "media" && (
            <ErrorBoundary fallback={<p role="alert">媒体矩阵出错，请刷新重试</p>}>
              <AccountsWorkspace />
            </ErrorBoundary>
          )}
          {activeNav === "tasks" && (
            <ErrorBoundary fallback={<p role="alert">分发引擎出错，请刷新重试</p>}>
              <TasksWorkspace />
            </ErrorBoundary>
          )}
          {activeNav === "system" && (
            <ErrorBoundary fallback={<p role="alert">系统状态出错，请刷新重试</p>}>
              <SystemWorkspace />
            </ErrorBoundary>
          )}
        </div>
      </section>
    </main>
  );
}
