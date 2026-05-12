import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../../api/client";

export interface User {
  id: number;
  username: string;
  role: "admin" | "operator";
  must_change_password: boolean;
}

export interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (oldPassword: string, newPassword: string) => Promise<void>;
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
    api<{ id: number; username: string; role: string; must_change_password: boolean }>("/api/auth/me")
      .then((data) => {
        setUser({
          id: data.id,
          username: data.username,
          role: data.role as "admin" | "operator",
          must_change_password: data.must_change_password,
        });
      })
      .catch(() => {
        setUser(null);
      })
      .finally(() => {
        setLoading(false);
      });
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    const data = await api<{ id: number; username: string; role: string; must_change_password: boolean }>("/api/auth/me");
    setUser({
      id: data.id,
      username: data.username,
      role: data.role as "admin" | "operator",
      must_change_password: data.must_change_password,
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
