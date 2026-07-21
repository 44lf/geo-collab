import { api } from "./core";
import type {
  GameCreateRequest,
  GameDetail,
  GameIngestConfig,
  GameIngestConfigPatch,
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
  limit?: number;
  offset?: number;
}): Promise<GameListResponse> {
  const p = new URLSearchParams();
  if (params?.tag) p.set("tag", params.tag);
  if (params?.min_score != null) p.set("min_score", String(params.min_score));
  if (params?.q) p.set("q", params.q);
  if (params?.kind) p.set("kind", params.kind);
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
