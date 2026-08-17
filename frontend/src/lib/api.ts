// Thin fetch wrapper for the partner-mappings endpoints. Kept separate from
// components so the page component only deals with data, not fetch/error
// plumbing -- and so when we add batches/rules/etc. later, each gets its
// own small file here instead of one giant "api.ts" god-file.

export type PartnerMapping = {
  id: number;
  member_code: string;
  bucket: "aggregator" | "bank_wallet";
  partner_name: string;
  institution_label: string | null;
  active: boolean;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Request failed: ${res.status}`);
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export type Batch = {
  id: number;
  name: string;
  status: "open" | "finished";
  created_at: string;
  finished_at: string | null;
  notes: string | null;
};

export type IssueStatusValue = "pending" | "in_progress" | "solved" | "exclude" | "lo_progress" | "success";

export type MidOverride = { status: IssueStatusValue; remark?: string };

export type Issue = {
  id: number | null;
  side?: "sct" | "aggregator" | "bank" | "unknown";
  category: string;
  count: number;
  affected_mids: string[];
  status: IssueStatusValue;
  comment: string | null;
  mid_overrides?: Record<string, MidOverride>;
  last_solved_comment?: string | null;
};

export type PartnerSummary = {
  partner_name: string;
  failed: number;
  pending: number;
  lo_progress: number;
  issues_failed: Issue[];
  issues_pending: Issue[];
  issues_lo_progress: Issue[];
};

export type DashboardData = {
  totals: {
    total_transactions: number;
    pending: number;
    failed: number;
    settlement_failed: number;
    transaction_failed: number;
    no_aggregator: number;
    total_aggregators: number;
    lo_progress: number;
    success_issues: number;
    /** Failures a later reprocess already settled — excluded from every count above. */
    retry_resolved: number;
  };
  aggregator_summary: PartnerSummary[];
  bank_summary: PartnerSummary[];
  sct_summary: {
    total_issues: number;
    issues_failed: Issue[];
    issues_pending: Issue[];
    issues_lo_progress: Issue[];
  };
};

export type BatchWithDashboard = { batch: Batch; dashboard: DashboardData };

// ── Error Classification ─────────────────────────────────────────────────────
// A different question from the Solve view: not "what is left to fix" but
// "what broke, on whose side, how often". Counts every non-success txn
// regardless of ops status, so it is not filtered by solved/excluded.

export type ErrorSide = "sct" | "aggregator" | "bank" | "unknown";
export type EntityType = "aggregator" | "bank_wallet" | "sct" | "unmapped";

export type ErrorCategoryRow = {
  category: string;
  side: ErrorSide;
  count: number;
  failed: number;
  pending: number;
  lo_progress: number;
  share_of_entity: number;
  share_of_batch: number;
};

export type ErrorEntity = {
  entity: string;
  entity_type: EntityType;
  total: number;
  failed: number;
  pending: number;
  lo_progress: number;
  share_of_batch: number;
  categories: ErrorCategoryRow[];
};

/** One failure that a later reprocess settled — the audit trail behind the suppression. */
export type RetryResolvedRow = {
  mid: string | null;
  merchant_name: string | null;
  partner_name: string | null;
  category: string | null;
  amount: number | null;
  beneficiary_id: string | null;
  failed_at: string | null;
  failed_settled_by: string | null;
  failed_stan: string;
  failed_remark: string | null;
  settled_at: string | null;
  settled_by: string | null;
  settled_stan: string;
  same_batch: boolean;
};

export type ErrorClassificationData = {
  batch: Batch;
  totals: {
    total_errors: number;
    entities: number;
    categories: number;
    by_status: { failed: number; pending: number; lo_progress: number };
    by_side: Record<string, number>;
    retry_resolved: number;
  };
  entities: ErrorEntity[];
  categories: {
    category: string;
    side: ErrorSide;
    count: number;
    entity_count: number;
    share_of_batch: number;
  }[];
  retry_resolved_rows: RetryResolvedRow[];
};

export const batchesApi = {
  upload: async (file: File): Promise<BatchWithDashboard> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/batches/upload", { method: "POST", body: formData });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `Upload failed: ${res.status}`);
    }
    return res.json();
  },

  list: () => request<Batch[]>("/batches"),

  get: (id: number) => request<BatchWithDashboard>(`/batches/${id}`),

  updateNotes: (id: number, notes: string) =>
    request<Batch>(`/batches/${id}/notes`, { method: "PATCH", body: JSON.stringify({ notes }) }),

  finish: (id: number) => request<Batch>(`/batches/${id}/finish`, { method: "POST" }),

  getReportUrl: (id: number) => `/api/batches/${id}/report`,

  errorClassification: (id: number) =>
    request<ErrorClassificationData>(`/batches/${id}/error-classification`),

  getErrorClassificationUrl: (id: number) =>
    `/api/batches/${id}/error-classification/export`,

  getAggregatorReportUrl: (batchId: number, partnerName: string, status?: string) => 
    `/api/batches/${batchId}/export-aggregator/${encodeURIComponent(partnerName)}${status ? `?status=${status}` : ""}`,

  updateIssue: (batchId: number, issueId: number, data: { status?: IssueStatusValue; comment?: string; mid_overrides?: Record<string, MidOverride> }) =>
    request<Issue>(`/batches/${batchId}/issues/${issueId}`, { method: "PATCH", body: JSON.stringify(data) }),

  remove: (id: number) => request<void>(`/batches/${id}`, { method: "DELETE" }),
};

export const partnerMappingsApi = {
  list: (bucket?: "aggregator" | "bank_wallet") =>
    request<PartnerMapping[]>(`/partner-mappings${bucket ? `?bucket=${bucket}` : ""}`),

  create: (data: {
    member_code: string;
    bucket: "aggregator" | "bank_wallet";
    partner_name: string;
    institution_label?: string;
  }) => request<PartnerMapping>("/partner-mappings", { method: "POST", body: JSON.stringify(data) }),

  bulkImport: (data: { partner_name: string; member_codes: string[] }) =>
    request<{ created: string[]; skipped: { code: string; reason: string }[]; created_count: number }>(
      "/partner-mappings/bulk-import",
      { method: "POST", body: JSON.stringify(data) }
    ),

  update: (id: number, data: Partial<Pick<PartnerMapping, "partner_name" | "bucket" | "institution_label" | "active">>) =>
    request<PartnerMapping>(`/partner-mappings/${id}`, { method: "PUT", body: JSON.stringify(data) }),

  remove: (id: number) => request<void>(`/partner-mappings/${id}`, { method: "DELETE" }),

  removeAggregator: (partnerName: string) =>
    request<void>(`/partner-mappings/aggregators/${encodeURIComponent(partnerName)}`, { method: "DELETE" }),

  aggregatorNames: () => request<string[]>("/partner-mappings/aggregators"),
};
