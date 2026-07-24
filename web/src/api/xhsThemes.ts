import { api } from "./core";

export interface XhsThemePreview {
  name: string;
  label: string;
  cover_url: string;
  card_url: string;
  cached: boolean;
}

export interface XhsGalleryState {
  themes: XhsThemePreview[];
  /** 服务端是否正在后台渲染预览（启动预热或手动重生） */
  generating: boolean;
}

interface Envelope<T> {
  ok: boolean;
  data: T;
  error: string | null;
}

export async function listXhsThemes(): Promise<XhsGalleryState> {
  const res = await api<Envelope<XhsGalleryState>>("/api/xhs-cards/themes");
  return res.data;
}

export async function regenerateXhsThemePreviews(): Promise<void> {
  await api<Envelope<{ status: string }>>("/api/xhs-cards/themes/regenerate", {
    method: "POST",
  });
}
