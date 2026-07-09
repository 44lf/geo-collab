import { api } from "./core";
import type { VideoListResponse } from "../types";

export function listVideos(params: {
  status?: "done" | "failed";
  skip: number;
  limit: number;
}): Promise<VideoListResponse> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  qs.set("skip", String(params.skip));
  qs.set("limit", String(params.limit));
  return api<VideoListResponse>(`/api/videos?${qs.toString()}`);
}
