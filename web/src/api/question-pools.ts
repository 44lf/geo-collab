import { api } from "./core";
import type { QuestionItem, QuestionPool, QuestionSyncResult, QuestionType } from "../types";

export function listQuestionPools(): Promise<QuestionPool[]> {
  return api<QuestionPool[]>("/api/generation/question-pools");
}

export function createQuestionPool(payload: {
  name: string;
  feishu_app_token?: string;
  feishu_table_id?: string;
}): Promise<QuestionPool> {
  return api<QuestionPool>("/api/generation/question-pools", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function syncQuestionPool(poolId: number): Promise<QuestionSyncResult> {
  return api<QuestionSyncResult>(`/api/generation/question-pools/${poolId}/sync`, {
    method: "POST",
  });
}

export function updateQuestionPool(
  poolId: number,
  payload: {
    name?: string;
    feishu_app_token?: string;
    feishu_table_id?: string;
    auto_sync_enabled?: boolean;
  },
): Promise<QuestionPool> {
  return api<QuestionPool>(`/api/generation/question-pools/${poolId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export function deleteQuestionPool(poolId: number): Promise<void> {
  return api<void>(`/api/generation/question-pools/${poolId}`, { method: "DELETE" });
}

export function listQuestionItems(poolId: number, status = "pending"): Promise<QuestionItem[]> {
  return api<QuestionItem[]>(`/api/generation/question-pools/${poolId}/items?status=${status}`);
}

export function listQuestionTypes(poolId: number): Promise<QuestionType[]> {
  return api<QuestionType[]>(`/api/generation/question-pools/${poolId}/question-types`);
}
