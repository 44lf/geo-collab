import { api } from "./core";
import type {
  GenerationSession,
  Scheme,
  SchemeCreatePayload,
  SchemeRun,
  SchemeRunSummary,
  SchemeUpdatePayload,
} from "../types";

export {
  createPromptTemplate,
  deletePromptTemplate,
  listPromptTemplates,
  patchPromptTemplate,
  updatePromptTemplate,
} from "./prompt-templates";

export function startGeneration(payload: {
  skill_id: number;
  prompt_template_id: number;
  extra_instruction?: string;
  pool_id?: number;
  question_item_ids?: number[];
  auto_count?: number;
}): Promise<{ session_id: number; status: string }> {
  return api<{ session_id: number; status: string }>("/api/generation/sessions", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function getGenerationSession(sessionId: number): Promise<GenerationSession> {
  return api<GenerationSession>(`/api/generation/sessions/${sessionId}`);
}

export function listSchemes(): Promise<Scheme[]> {
  return api<Scheme[]>("/api/generation/schemes");
}

export function getScheme(schemeId: number): Promise<Scheme> {
  return api<Scheme>(`/api/generation/schemes/${schemeId}`);
}

export function createScheme(payload: SchemeCreatePayload): Promise<Scheme> {
  return api<Scheme>("/api/generation/schemes", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function updateScheme(schemeId: number, payload: SchemeUpdatePayload): Promise<Scheme> {
  return api<Scheme>(`/api/generation/schemes/${schemeId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function patchScheme(
  schemeId: number,
  payload: { is_enabled?: boolean },
): Promise<Scheme> {
  return api<Scheme>(`/api/generation/schemes/${schemeId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteScheme(schemeId: number): Promise<void> {
  return api<void>(`/api/generation/schemes/${schemeId}`, { method: "DELETE" });
}

export function startSchemeRun(schemeId: number): Promise<{ run_id: number; status: string }> {
  return api<{ run_id: number; status: string }>(
    `/api/generation/schemes/${schemeId}/runs`,
    { method: "POST" },
  );
}

export function listSchemeRuns(schemeId: number): Promise<SchemeRunSummary[]> {
  return api<SchemeRunSummary[]>(`/api/generation/schemes/${schemeId}/runs`);
}

export function getSchemeRun(runId: number): Promise<SchemeRun> {
  return api<SchemeRun>(`/api/generation/scheme-runs/${runId}`);
}
