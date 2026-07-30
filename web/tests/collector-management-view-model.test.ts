import assert from "node:assert/strict";
import test from "node:test";

import {
  nodeHistoryEntries,
  transferReceiptEvidence,
} from "../src/features/collector/collectorManagementViewModel.ts";

test("节点状态映射保留最后成功与最后错误时间", () => {
  const node = {
    last_success_at: "2026-07-29T08:00:00Z",
    last_error_at: "2026-07-29T08:30:00Z",
  } as Parameters<typeof nodeHistoryEntries>[0];

  assert.deepEqual(nodeHistoryEntries(node), [
    { label: "最后成功", timestamp: "2026-07-29T08:00:00Z" },
    { label: "最后错误", timestamp: "2026-07-29T08:30:00Z" },
  ]);
});

test("失败回执映射保留分类、摘要与回放证据引用", () => {
  const transfer = {
    receipt: {
      status: "dead_letter",
      item_count: 2,
      completed_item_count: 1,
      error_classification: "schema",
      error_summary: "bundle schema mismatch",
      replay_evidence_ref: "evidence/transport-1",
      completed_at: "2026-07-29T09:00:00Z",
    },
  } as Parameters<typeof transferReceiptEvidence>[0];

  assert.deepEqual(transferReceiptEvidence(transfer), {
    status: "dead_letter",
    itemCount: 2,
    completedItemCount: 1,
    errorClassification: "schema",
    errorSummary: "bundle schema mismatch",
    replayEvidenceRef: "evidence/transport-1",
    completedAt: "2026-07-29T09:00:00Z",
  });
});

test("尚无回执时返回空映射", () => {
  const transfer = {
    receipt: null,
  } as Parameters<typeof transferReceiptEvidence>[0];

  assert.equal(transferReceiptEvidence(transfer), null);
});
