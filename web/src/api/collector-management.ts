import { api } from "./core";

export type CollectorNodeView = {
  id: number;
  collector_id: string;
  display_name: string;
  destination: string;
  status: "enabled" | "disabled" | "revoked";
  freshness: "online" | "stale" | "never_seen";
  platform: string | null;
  agent_version: string | null;
  enabled_sources: string[];
  current_run_id: string | null;
  current_stage: string | null;
  spool_pending_count: number;
  last_heartbeat_at: string | null;
  last_success_at: string | null;
  last_error_at: string | null;
  last_error_summary: string | null;
};

export type CollectorNodesResponse = {
  items: CollectorNodeView[];
  next_cursor: number | null;
};

export type CollectorBacklogView = {
  counts: Record<"ready" | "processing" | "retry_wait" | "failed" | "dead_letter", number>;
  oldest_ready_at: string | null;
};

export type CollectorRunEvent = {
  event_id: string;
  transport_id: string | null;
  bundle_id: string | null;
  source: string | null;
  component: string;
  component_version: string;
  stage: string;
  event_type: string;
  level: string;
  payload: Record<string, unknown> | null;
  occurred_at: string;
  received_at: string;
};

export type CollectorRunView = {
  run: {
    collector_id: string;
    run_id: string;
    job_id: string;
    source: string | null;
    status: string;
    current_stage: string | null;
    summary: Record<string, unknown> | null;
    error_classification: string | null;
    error_summary: string | null;
    started_at: string | null;
    finished_at: string | null;
  };
  events: CollectorRunEvent[];
};

export type CollectorRunSummary = CollectorRunView["run"];

export type CollectorRunsResponse = {
  items: CollectorRunSummary[];
};

export type CollectorTransferView = {
  transfer: {
    collector_id: string;
    transport_id: string;
    job_id: string;
    run_id: string;
    bundle_id: string;
    destination: string;
    archive_size: number;
    archive_sha256: string;
    status: string;
    attempt_count: number;
    error_classification: string | null;
    error_summary: string | null;
    ready_at: string | null;
    processed_at: string | null;
  };
  receipt: {
    status: string;
    item_count: number;
    completed_item_count: number;
    archive_sha256: string;
    error_classification: string | null;
    error_summary: string | null;
    replay_evidence_ref: string | null;
    completed_at: string;
  } | null;
  items: {
    item_key: string;
    source: string;
    source_item_id: string | null;
    status: string;
    game_id: number | null;
    outcome: Record<string, unknown> | null;
    error_classification: string | null;
    error_summary: string | null;
    completed_at: string | null;
  }[];
};

export function listCollectorNodes(): Promise<CollectorNodesResponse> {
  return api<CollectorNodesResponse>("/api/collector-management/nodes?limit=100");
}

export function getCollectorBacklog(): Promise<CollectorBacklogView> {
  return api<CollectorBacklogView>("/api/collector-management/backlog");
}

export function getCollectorRun(runId: string): Promise<CollectorRunView> {
  return api<CollectorRunView>(`/api/collector-management/runs/${encodeURIComponent(runId)}`);
}

export function listCollectorRuns(): Promise<CollectorRunsResponse> {
  return api<CollectorRunsResponse>("/api/collector-management/runs?limit=50");
}

export function getCollectorTransfer(transportId: string): Promise<CollectorTransferView> {
  return api<CollectorTransferView>(
    `/api/collector-management/transfers/${encodeURIComponent(transportId)}`,
  );
}
