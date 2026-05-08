export const emptyDoc = { type: "doc", content: [{ type: "paragraph" }] };

let _token: string | null = null;

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
  return assetId ? `/api/assets/${assetId}` : null;
}

export function countWords(text: string): number {
  return text.split(/\s+/).filter(Boolean).length;
}
