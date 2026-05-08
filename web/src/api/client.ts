export const emptyDoc = { type: "doc", content: [{ type: "paragraph" }] };

let _token: string | null = null;
const inFlightKeys = new Set<string>();

export async function singleFlight<T>(key: string, fn: () => Promise<T>): Promise<T | undefined> {
  if (inFlightKeys.has(key)) return undefined;
  inFlightKeys.add(key);
  try {
    return await fn();
  } finally {
    inFlightKeys.delete(key);
  }
}

export function newClientRequestId(prefix: string): string {
  const cryptoObj = globalThis.crypto;
  const random = typeof cryptoObj?.randomUUID === "function"
    ? cryptoObj.randomUUID()
    : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `${prefix}-${random}`;
}

async function getToken(): Promise<string | null> {
  if (_token !== null) return _token;
  try {
    const res = await fetch("/api/bootstrap");
    const data = (await res.json()) as { token: string };
    _token = data.token || null;
  } catch {
    _token = null;
  }
  return _token;
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getToken();
  const isFormData = init?.body instanceof FormData;

  const headers: Record<string, string> = {};
  if (!isFormData) headers["Content-Type"] = "application/json";
  if (token) headers["X-Geo-Token"] = token;

  const response = await fetch(path, {
    ...init,
    headers: { ...headers, ...(init?.headers as Record<string, string>) },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail || `${response.status} ${response.statusText}`);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

export function assetSrc(assetId: string | null): string | null {
  if (!assetId) return null;
  return withAssetToken(`/api/assets/${assetId}`);
}

export function withAssetToken(url: string): string {
  if (!_token || !url.startsWith("/api/assets/")) return url;
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(_token)}`;
}

export function countWords(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}
