import { api } from "./core";
import type { AiEngine } from "../types";

export function listAiEngines(): Promise<AiEngine[]> {
  return api<AiEngine[]>("/api/generation/ai-engines");
}

export function listFormatEngines(): Promise<AiEngine[]> {
  return api<AiEngine[]>("/api/generation/format-engines");
}
