import { api } from "./core";

export interface H5LoginResponse {
  authenticated: boolean;
  reason?: string;
}

export interface H5BindResponse {
  bound: boolean;
  reason?: string;
}

/** 飞书 H5 免登：用 tt.requestAccess 拿到的 code 换 GEO 会话 cookie（公开接口，未登录可调）。 */
export function h5Login(code: string): Promise<H5LoginResponse> {
  return api<H5LoginResponse>("/api/feishu/h5-login", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}

/** 飞书 H5 首次登录后自动绑定 open_id（需已登录）。 */
export function h5Bind(code: string): Promise<H5BindResponse> {
  return api<H5BindResponse>("/api/feishu/h5-bind", {
    method: "POST",
    body: JSON.stringify({ code }),
  });
}
