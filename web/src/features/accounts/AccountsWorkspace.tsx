import { useEffect, useState } from "react";
import { api, authHeaders } from "../../api/client";
import type { Account } from "../../types";
import { CheckCircle2, Download, Plus, RefreshCw, Trash2, Upload, UserPlus, X } from "lucide-react";

export function AccountsWorkspace() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [displayName, setDisplayName] = useState("头条号账号");
  const [accountKey, setAccountKey] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [confirmDeleteAccount, setConfirmDeleteAccount] = useState<Account | null>(null);

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
      setDisplayName("头条号账号");
      setAccountKey("");
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

  async function importAuthPackage(file: File) {
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await api<{ imported: string[]; skipped: string[] }>("/api/accounts/import", { method: "POST", body: formData });
      await refreshAccounts();
      const msg = `导入完成：${result.imported.length} 个新增${result.skipped.length ? `，${result.skipped.length} 个已存在跳过` : ""}`;
      setNotice(msg);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "导入失败");
    } finally {
      setLoading(false);
    }
  }

  async function renameAccount(accountId: number) {
    if (!renameValue.trim()) return;
    setLoading(true);
    try {
      await api<Account>(`/api/accounts/${accountId}`, {
        method: "PATCH",
        body: JSON.stringify({ display_name: renameValue.trim() }),
      });
      await refreshAccounts();
      setRenamingId(null);
      setNotice("已重命名");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "重命名失败");
    } finally {
      setLoading(false);
    }
  }

  async function exportAuthPackage() {
    setLoading(true);
    try {
      const headers = { ...await authHeaders(), "Content-Type": "application/json" };
      const response = await fetch("/api/accounts/export", {
        method: "POST",
        headers,
        body: JSON.stringify({ account_ids: accounts.map((account) => account.id) }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || `${response.status} ${response.statusText}`);
      }

      const exportPath = response.headers.get("x-export-path") ?? "";
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
      setNotice(exportPath ? `已导出：${exportPath}` : "授权包已导出");
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
          <label className="secondaryButton" style={{ cursor: loading ? "not-allowed" : "pointer", opacity: loading ? 0.5 : 1 }}>
            <Upload size={16} />
            导入授权包
            <input
              type="file"
              accept=".zip"
              style={{ display: "none" }}
              disabled={loading}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) void importAuthPackage(file);
                e.target.value = "";
              }}
            />
          </label>
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
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                {renamingId === account.id ? (
                  <>
                    <input
                      autoFocus
                      value={renameValue}
                      style={{ flex: 1, fontSize: 14, fontWeight: 600 }}
                      onChange={(e) => setRenameValue(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void renameAccount(account.id);
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                    />
                    <button type="button" disabled={loading} onClick={() => void renameAccount(account.id)}>确定</button>
                    <button type="button" onClick={() => setRenamingId(null)}>取消</button>
                  </>
                ) : (
                  <>
                    <strong style={{ flex: 1 }}>{account.display_name}</strong>
                    <button type="button" style={{ fontSize: 12, padding: "2px 6px" }} onClick={() => { setRenamingId(account.id); setRenameValue(account.display_name); }}>改名</button>
                  </>
                )}
              </div>
              <span>{account.platform_name}</span>
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
                <button type="button" disabled={loading} onClick={() => setConfirmDeleteAccount(account)}>
                  <Trash2 size={15} />
                  删除
                </button>
              </div>
            </article>
          ))}
          {accounts.length === 0 ? <p className="emptyText">暂无授权账号</p> : null}
        </section>
      </section>

      {confirmDeleteAccount ? (
        <div className="modalBackdrop" role="presentation" onMouseDown={() => setConfirmDeleteAccount(null)}>
          <section className="groupPickerModal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
            <header className="modalHeader">
              <div>
                <h2>确认删除账号？</h2>
                <p>将同时清除该账号的本地授权状态，需要重新登录</p>
              </div>
              <button type="button" aria-label="关闭" onClick={() => setConfirmDeleteAccount(null)}>
                <X size={16} />
              </button>
            </header>
            <footer className="modalActions">
              <button type="button" onClick={() => setConfirmDeleteAccount(null)}>取消</button>
              <button type="button" className="dangerButton" disabled={loading} onClick={() => { const account = confirmDeleteAccount; setConfirmDeleteAccount(null); void remove(account); }}>确认删除</button>
            </footer>
          </section>
        </div>
      ) : null}
    </>
  );
}
