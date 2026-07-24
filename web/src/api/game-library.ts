import { api } from "./core";
import type {
  GameCreateRequest,
  GameDetail,
  GameIngestConfig,
  GameIngestConfigPatch,
  GameIngestLogList,
  GameIngestRunStartResponse,
  GameListResponse,
  GameTagCount,
  GameUpdateRequest,
  ImageCategoryImportRequest,
  ImageCategoryImportResponse,
} from "../types";

export function listGameTags(limit = 200): Promise<GameTagCount[]> {
  return api<GameTagCount[]>(`/api/game-library/tags?limit=${limit}`);
}

export function listGames(params?: {
  tag?: string;
  min_score?: number;
  q?: string;
  kind?: "main" | "companion";
  source?: string;
  sort?: "score" | "least_used" | "recent";
  limit?: number;
  offset?: number;
}): Promise<GameListResponse> {
  const p = new URLSearchParams();
  if (params?.tag) p.set("tag", params.tag);
  if (params?.min_score != null) p.set("min_score", String(params.min_score));
  if (params?.q) p.set("q", params.q);
  if (params?.kind) p.set("kind", params.kind);
  if (params?.source) p.set("source", params.source);
  if (params?.sort) p.set("sort", params.sort);
  if (params?.limit != null) p.set("limit", String(params.limit));
  if (params?.offset != null) p.set("offset", String(params.offset));
  const qs = p.toString();
  return api<GameListResponse>(qs ? `/api/game-library/games?${qs}` : "/api/game-library/games");
}

export function getGame(gameId: number): Promise<GameDetail> {
  return api<GameDetail>(`/api/game-library/games/${gameId}`);
}

export function createGame(payload: GameCreateRequest): Promise<GameDetail> {
  return api<GameDetail>("/api/game-library/games", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getIngestConfig(): Promise<GameIngestConfig> {
  return api<GameIngestConfig>("/api/game-library/ingest/config");
}

export function patchIngestConfig(patch: GameIngestConfigPatch): Promise<GameIngestConfig> {
  return api<GameIngestConfig>("/api/game-library/ingest/config", {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function startIngestRun(): Promise<GameIngestRunStartResponse> {
  return api<GameIngestRunStartResponse>("/api/game-library/ingest/run", { method: "POST" });
}

// 扩库（应用宝榜单发现）立即跑一批。补全（按名巡检）走上面的 startIngestRun。
export function startDiscoveryRun(): Promise<GameIngestRunStartResponse> {
  return api<GameIngestRunStartResponse>("/api/game-library/ingest/discovery/run", {
    method: "POST",
  });
}

// 抓取/导入运行日志：复用通用 report_events(user JWT)，按 source_module=game_ingest 过滤。
export function listGameIngestLogs(params?: {
  limit?: number;
  cursor?: number;
}): Promise<GameIngestLogList> {
  const p = new URLSearchParams();
  p.set("source_module", "game_ingest");
  p.set("limit", String(params?.limit ?? 30));
  if (params?.cursor != null) p.set("cursor", String(params.cursor));
  return api<GameIngestLogList>(`/api/report-events?${p.toString()}`);
}

export function importImageCategories(
  payload?: ImageCategoryImportRequest,
): Promise<ImageCategoryImportResponse> {
  return api<ImageCategoryImportResponse>("/api/game-library/import-image-categories", {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

export function updateGame(gameId: number, patch: GameUpdateRequest): Promise<GameDetail> {
  return api<GameDetail>(`/api/game-library/games/${gameId}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export function deleteGame(gameId: number): Promise<void> {
  return api<void>(`/api/game-library/games/${gameId}`, { method: "DELETE" });
}
