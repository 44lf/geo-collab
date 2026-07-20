import { api } from "./core";
import type { GameDetail, GameListResponse, GameTagCount } from "../types";

export function listGameTags(limit = 200): Promise<GameTagCount[]> {
  return api<GameTagCount[]>(`/api/game-library/tags?limit=${limit}`);
}

export function listGames(params?: {
  tag?: string;
  min_score?: number;
  q?: string;
  limit?: number;
  offset?: number;
}): Promise<GameListResponse> {
  const p = new URLSearchParams();
  if (params?.tag) p.set("tag", params.tag);
  if (params?.min_score != null) p.set("min_score", String(params.min_score));
  if (params?.q) p.set("q", params.q);
  if (params?.limit != null) p.set("limit", String(params.limit));
  if (params?.offset != null) p.set("offset", String(params.offset));
  const qs = p.toString();
  return api<GameListResponse>(qs ? `/api/game-library/games?${qs}` : "/api/game-library/games");
}

export function getGame(gameId: number): Promise<GameDetail> {
  return api<GameDetail>(`/api/game-library/games/${gameId}`);
}
