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

export type IssueStatusValue = "pending" | "in_progress" | "solved";

export type Issue = {
  id: number | null;
  side?: "sct" | "aggregator" | "bank" | "unknown";
  category: string;
  count: number;
  affected_mids: string[];
  status: IssueStatusValue;
  comment: string | null;
};

export type PartnerSummary = {
  partner_name: string;
  failed: number;
  pending: number;
  issue_count: number;
  issues: Issue[];
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
  };
  aggregator_summary: PartnerSummary[];
  bank_summary: PartnerSummary[];
  sct_summary: { total_issues: number; issues: Issue[] };
};

export type BatchWithDashboard = { batch: Batch; dashboard: DashboardData };

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

  updateIssue: (batchId: number, issueId: number, data: { status?: IssueStatusValue; comment?: string }) =>
    request<Issue>(`/batches/${batchId}/issues/${issueId}`, { method: "PATCH", body: JSON.stringify(data) }),
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

  aggregatorNames: () => request<string[]>("/partner-mappings/aggregators"),
};
