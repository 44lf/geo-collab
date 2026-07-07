import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, h5Bind, h5Login } from "../../api/client";
import { getFeishuAuthCode, inFeishu } from "./feishuH5";

export interface User {
  id: number;
  username: string;
  role: "admin" | "operator";
  must_change_password: boolean;
  ai_format_preset_id: number | null;
}

export interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
}

/** `/api/auth/me` 响应形状（额外带 feishu_open_id，用于判断是否需要自动绑定）。 */
interface MeResponse {
  id: number;
  username: string;
  role: string;
  must_change_password: boolean;
  ai_format_preset_id: number | null;
  feishu_open_id: string | null;
}

function toUser(data: MeResponse): User {
  return {
    id: data.id,
    username: data.username,
    role: data.role as "admin" | "operator",
    must_change_password: data.must_change_password,
    ai_format_preset_id: data.ai_format_preset_id,
  };
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    function handleUnauthorized() {
      setUser(null);
    }
    function handlePasswordChangeRequired() {
      setUser((prev) => (prev ? { ...prev, must_change_password: true } : null));
    }
    window.addEventListener("auth:unauthorized", handleUnauthorized);
    window.addEventListener("auth:password-change-required", handlePasswordChangeRequired);
    return () => {
      window.removeEventListener("auth:unauthorized", handleUnauthorized);
      window.removeEventListener("auth:password-change-required", handlePasswordChangeRequired);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;

    async function init() {
      try {
        // 常规路径：已有有效 GEO 会话 cookie（含飞书场景下上次 h5-login 种下的 cookie）。
        const data = await api<MeResponse>("/api/auth/me");
        if (cancelled) return;
        setUser(toUser(data));
        // 已登录但账号还没绑定飞书 open_id：飞书容器内静默换 code 自动补绑，
        // fire-and-forget——不阻塞 loading、不影响当前登录态，失败也忽略。
        if (inFeishu() && data.feishu_open_id == null) {
          getFeishuAuthCode()
            .then((code) => (code ? h5Bind(code) : undefined))
            .catch(() => {});
        }
      } catch {
        if (cancelled) return;
        if (!inFeishu()) {
          // 非飞书场景：行为与改造前完全一致——未登录就是未登录。
          setUser(null);
          return;
        }
        // 飞书场景：尝试免登（拿 code 换 GEO 会话 cookie），成功则重新拉 /me。
        try {
          const code = await getFeishuAuthCode();
          if (!code) {
            setUser(null);
            return;
          }
          const loginResult = await h5Login(code);
          if (!loginResult.authenticated) {
            setUser(null);
            return;
          }
          const data = await api<MeResponse>("/api/auth/me");
          if (!cancelled) setUser(toUser(data));
        } catch {
          if (!cancelled) setUser(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    init();

    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    const data = await api<{ id: number; username: string; role: string; must_change_password: boolean; ai_format_preset_id: number | null }>("/api/auth/me");
    setUser({
      id: data.id,
      username: data.username,
      role: data.role as "admin" | "operator",
      must_change_password: data.must_change_password,
      ai_format_preset_id: data.ai_format_preset_id,
    });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
    }
  }, []);

  const changePassword = useCallback(async (oldPassword: string, newPassword: string) => {
    await api("/api/auth/change-password", {
      method: "POST",
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    });
    setUser((prev) => (prev ? { ...prev, must_change_password: false } : null));
  }, []);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, changePassword }}>
      {children}
    </AuthContext.Provider>
  );
}
