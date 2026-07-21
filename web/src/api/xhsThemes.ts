import { api } from "./core";

export interface XhsThemePreview {
  name: string;
  label: string;
  cover_url: string;
  card_url: string;
  cached: boolean;
}

interface Envelope<T> {
  ok: boolean;
  data: T;
  error: string | null;
}

export async function listXhsThemes(): Promise<XhsThemePreview[]> {
  const res = await api<Envelope<XhsThemePreview[]>>("/api/xhs-cards/themes");
  return res.data;
}

export async function regenerateXhsThemePreviews(): Promise<void> {
  await api<Envelope<{ status: string }>>("/api/xhs-cards/themes/regenerate", {
    method: "POST",
  });
}
