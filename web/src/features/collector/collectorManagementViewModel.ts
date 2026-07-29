import type { CollectorNodeView, CollectorTransferView } from "../../api/collector-management";

export type NodeHistoryEntry = {
  label: "最后成功" | "最后错误";
  timestamp: string | null;
};

export type TransferReceiptEvidence = {
  status: string;
  itemCount: number;
  completedItemCount: number;
  errorClassification: string | null;
  errorSummary: string | null;
  replayEvidenceRef: string | null;
  completedAt: string;
};

export function nodeHistoryEntries(node: CollectorNodeView): NodeHistoryEntry[] {
  return [
    { label: "最后成功", timestamp: node.last_success_at },
    { label: "最后错误", timestamp: node.last_error_at },
  ];
}

export function transferReceiptEvidence(
  transfer: CollectorTransferView,
): TransferReceiptEvidence | null {
  if (!transfer.receipt) return null;
  return {
    status: transfer.receipt.status,
    itemCount: transfer.receipt.item_count,
    completedItemCount: transfer.receipt.completed_item_count,
    errorClassification: transfer.receipt.error_classification,
    errorSummary: transfer.receipt.error_summary,
    replayEvidenceRef: transfer.receipt.replay_evidence_ref,
    completedAt: transfer.receipt.completed_at,
  };
}
