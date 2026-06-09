// 后端所有时间戳已是带 "Z" 的 UTC 字符串（main.py 的序列化补丁统一追加），
// 这里直接 new Date(isoString) 解析即可；切勿再追加 "Z"（会变成 "...ZZ" → Invalid Date）。
function parseUtc(isoString: string | null | undefined): Date | null {
  if (!isoString) return null;
  const d = new Date(isoString);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function formatDateTime(isoString: string | null | undefined): string {
  const d = parseUtc(isoString);
  return d ? d.toLocaleString("zh-CN") : "—";
}

export function formatDate(isoString: string | null | undefined): string {
  const d = parseUtc(isoString);
  return d ? d.toLocaleDateString("zh-CN") : "—";
}

export function formatTime(isoString: string | null | undefined): string {
  const d = parseUtc(isoString);
  return d ? d.toLocaleTimeString("zh-CN") : "—";
}
