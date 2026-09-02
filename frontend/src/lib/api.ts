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

/**
 * Statuses that mean ops has decided what happens to an issue, so it is not
 * outstanding work. "exclude" is a deliberate "not needed" and "success" is an
 * affirmative outcome -- neither should be reported as unsolved when finishing
 * a batch. Only pending / in_progress / lo_progress are still open.
 *
 * Deliberately NOT used by Analytics or Error Classification: those count an
 * excluded issue on purpose, because it still happened (see
 * analytics_service.build_analytics). This is about outstanding work, not
 * about what occurred.
 */
const DECIDED_ISSUE_STATUSES: ReadonlySet<IssueStatusValue> = new Set<IssueStatusValue>([
  "solved",
  "exclude",
  "success",
]);

export function isIssueDecided(status: IssueStatusValue): boolean {
  return DECIDED_ISSUE_STATUSES.has(status);
}

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

// ── Analytics ─────────────────────────────────────────────────────────────
// Cross-batch: every batch whose processing date falls in [from, to] is
// included (open or finished), unlike every other endpoint here which is
// scoped to one batch_id. See backend/app/services/analytics_service.py.

export type AnalyticsBucket = "day" | "week" | "month";

export type AnalyticsTrendPoint = {
  bucket_label: string;
  bucket_start: string;
  total: number;
  failed: number;
  pending: number;
  lo_progress: number;
  solved: number;
  unsolved: number;
};

export type AnalyticsEntity = {
  entity: string;
  entity_type: EntityType;
  total: number;
  failed: number;
  pending: number;
  lo_progress: number;
  solved: number;
  unsolved: number;
};

/** Every entity seen in the range, regardless of the current exclude filter — used to populate the exclude picker so toggling one back on is always possible. */
export type AnalyticsAvailableEntity = { entity: string; entity_type: EntityType };

export type AnalyticsCategory = {
  category: string;
  side: ErrorSide;
  count: number;
  share_of_batch: number;
};

export type AnalyticsData = {
  range: { from: string; to: string; bucket: AnalyticsBucket };
  kpis: {
    total_transactions: number;
    total_errors: number;
    solved: number;
    unsolved: number;
    /** Issues ops set aside. Counted here only — they are not in total_errors,
     *  the status split, the trend, the entities or the categories. */
    excluded: number;
    /** solved / total_errors, where total_errors already omits excluded. */
    resolution_rate: number;
    batches_included: number;
  };
  trend: AnalyticsTrendPoint[];
  status_breakdown: { failed: number; pending: number; lo_progress: number };
  resolution_breakdown: { solved: number; unsolved: number; excluded: number };
  top_entities: AnalyticsEntity[];
  top_categories: AnalyticsCategory[];
  available_entities: AnalyticsAvailableEntity[];
};

export const analyticsApi = {
  get: (from: string, to: string, bucket: AnalyticsBucket, excludeEntities: string[] = []) => {
    const params = new URLSearchParams({ from, to, bucket });
    excludeEntities.forEach((e) => params.append("exclude_entities", e));
    return request<AnalyticsData>(`/analytics?${params.toString()}`);
  },
};

// ── Settlement Type Report ───────────────────────────────────────────────
// The success-side counterpart to Analytics: of what settled, how did it
// settle (Real Time / System Default / On Call), broken down by entity.
// Same date-range-over-existing-batches scope, no upload of its own. See
// backend/app/services/settlement_type_service.py.

export type SettlementMethodCounts = {
  real_time: number;
  system_default: number;
  on_call: number;
  unknown: number;
};

export type SettlementTypeEntity = SettlementMethodCounts & {
  entity: string;
  entity_type: EntityType;
  total: number;
  /** Total settled amount for this entity, in NPR. */
  amount: number;
};

export type SettlementMethodAmounts = {
  real_time: number;
  system_default: number;
  on_call: number;
  unknown: number;
};

export type SettlementTypeData = {
  range: { from: string; to: string };
  kpis: SettlementMethodCounts & {
    total_settled: number;
    total_amount_settled: number;
    batches_included: number;
  };
  method_breakdown: SettlementMethodCounts;
  method_amount_breakdown: SettlementMethodAmounts;
  entities: SettlementTypeEntity[];
};

// Document Analysis: same Settlement Type breakdown for one uploaded file
// that never becomes a batch -- nothing is persisted, no Batch/Transaction
// row is created. See backend/app/services/adhoc_settlement_service.py.

export type AdhocSettlementTypeData = {
  file_name: string;
  row_count: number;
  kpis: SettlementMethodCounts & { total_settled: number; total_amount_settled: number };
  method_breakdown: SettlementMethodCounts;
  method_amount_breakdown: SettlementMethodAmounts;
  entities: SettlementTypeEntity[];
};

// Both report downloads are POST/GET-of-bytes rather than JSON, so they're
// triggered the same way: fetch the blob, hand it to the browser via a
// throwaway object URL. batchesApi's report downloads can use a plain
// <a href download> because they're GET; the ad-hoc one is a POST (it has
// to resend the file, since nothing was persisted server-side to refetch).
async function downloadBlob(url: string, options: RequestInit, fallbackName: string): Promise<void> {
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || `Download failed: ${res.status}`);
  }
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^"]+)"?/);
  const filename = match ? match[1] : fallbackName;

  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

export const settlementTypeApi = {
  get: (from: string, to: string) =>
    request<SettlementTypeData>(`/settlement-type?from=${from}&to=${to}`),

  downloadReport: (from: string, to: string) =>
    downloadBlob(
      `/api/settlement-type/report?from=${from}&to=${to}`,
      { method: "GET" },
      `Settlement_Type_Report_${from}_to_${to}.xlsx`
    ),

  analyzeDocument: async (file: File): Promise<AdhocSettlementTypeData> => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch("/api/settlement-type/adhoc-analysis", { method: "POST", body: formData });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `Analysis failed: ${res.status}`);
    }
    return res.json();
  },

  /**
   * `txnFile` is that day's Transaction List, and is optional. When it's
   * sent, the report's per-entity sheets pair every settled MID with the
   * transaction it settled; without it the report is unchanged.
   */
  downloadDocumentReport: (file: File, txnFile?: File | null) => {
    const formData = new FormData();
    formData.append("file", file);
    if (txnFile) formData.append("txn_file", txnFile);
    return downloadBlob(
      "/api/settlement-type/adhoc-analysis/report",
      { method: "POST", body: formData },
      `Settlement_Type_Report_${file.name.replace(/\.[^/.]+$/, "")}.xlsx`
    );
  },
};

// ── Settings & batch summary email ───────────────────────────────────────
// Operator-editable mail/SMTP config, and the end-of-batch summary email it
// drives. See backend/app/services/settings_service.py and mail_service.py.

export type SettingField = {
  key: string;
  label: string;
  group: "mail" | "smtp";
  type: "str" | "int" | "bool";
  secret: boolean;
  default: string;
};

/** Whether mail can be sent, without revealing any of the transport. The
 *  smtp_* values are configured in backend/.env and never leave the server. */
export type SmtpStatus = {
  configured: boolean;
  host_set: boolean;
  credentials_set: boolean;
  from_domain: string;
};

export type SettingsPayload = {
  values: Record<string, string | number | boolean>;
  schema: SettingField[];
  smtp: SmtpStatus;
};

/** Sent in place of a stored secret; echo it back unchanged to leave it alone. */
export const SECRET_MASK = "••••••••";

export const settingsApi = {
  get: () => request<SettingsPayload>("/settings"),

  save: (values: Record<string, string | number | boolean>) =>
    request<SettingsPayload>("/settings", { method: "PUT", body: JSON.stringify(values) }),

  sendTest: (to?: string) =>
    request<{ sent_to: string[] }>("/settings/test-email", {
      method: "POST",
      body: JSON.stringify(to ? { to } : {}),
    }),
};

export type BatchEmailStats = {
  total_transactions: number;
  total_errors: number;
  status_breakdown: { failed: number; pending: number; lo_progress: number };
  resolution: { solved: number; unsolved: number };
  excluded: number;
  retry_resolved: number;
  resolution_rate: number;
};

/** The editable draft shown in the send-summary overlay. */
export type BatchEmailDraft = {
  batch: Batch;
  subject: string;
  from_addr: string;
  from_name: string;
  to: string;
  cc: string;
  body_html: string;
  /** Base64 PNG of the Errors & Resolution ring, embedded inline on send. */
  chart_png_base64: string;
  /** Base64 PNG of error volume per aggregator, also embedded inline. */
  volume_png_base64: string;
  /** Appended below the body on send. SMTP bypasses Outlook, so the signature
   *  configured there never applies — this is the one that does. */
  signature_html: string;
  /** Whether to attach the full Excel report — the same workbook Download
   *  Full Report produces. Built at send time, not for the preview. */
  attach_report: boolean;
  report_filename: string;
  /** Base64 logo shown under the signature, embedded inline on send. Empty
   *  unless MAIL_SIGNATURE_LOGO points at a real file. */
  signature_logo_base64: string;
  stats: BatchEmailStats;
  smtp_configured: boolean;
};

export const batchEmailApi = {
  preview: (batchId: number) => request<BatchEmailDraft>(`/batches/${batchId}/email-preview`),

  send: (
    batchId: number,
    // The logo is read from disk by the server (MAIL_SIGNATURE_LOGO), so it is
    // preview-only and deliberately not round-tripped through the browser.
    draft: Omit<
      BatchEmailDraft,
      "batch" | "stats" | "smtp_configured" | "report_filename" | "signature_logo_base64"
    > & { batch_name?: string }
  ) =>
    request<{
      sent_to: string[];
      cc: string[];
      subject: string;
      attachment: { filename: string; bytes: number } | null;
    }>(`/batches/${batchId}/send-email`, { method: "POST", body: JSON.stringify(draft) }),
};

// ── Issuer / Acquirer reconciliation ─────────────────────────────────────
// Upload one transaction export and (optionally) one settlement export.
// Inside the transaction file issuing and acquiring always balance; the gap
// worth looking at is transacted vs settled. See
// backend/app/services/issuer_acquirer_service.py.

export type IssuingRow = {
  name: string;
  txn_count: number;
  txn_amount: number;
  share: number;
};

export type AcquiringRow = {
  name: string;
  txn_count: number;
  txn_amount: number;
  settled_count: number;
  settled_amount: number;
  /** Positive = transacted but not yet settled. */
  variance_amount: number;
  variance_count: number;
};

export type IssuerAcquirerData = {
  totals: {
    txn_rows: number;
    txn_amount: number;
    settlement_rows: number;
    settled_count: number;
    settled_amount: number;
    variance_amount: number;
    variance_count: number;
    settled_pct: number;
    window: string;
    has_settlement: boolean;
  };
  issuing: IssuingRow[];
  acquiring: AcquiringRow[];
  files: { transaction: string; settlement: string };
};

function issuerAcquirerForm(txnFile: File, settlementFile?: File | null) {
  const fd = new FormData();
  fd.append("txn_file", txnFile);
  if (settlementFile) fd.append("settlement_file", settlementFile);
  return fd;
}

export const issuerAcquirerApi = {
  analyze: async (txnFile: File, settlementFile?: File | null): Promise<IssuerAcquirerData> => {
    const res = await fetch("/api/issuer-acquirer/analyze", {
      method: "POST",
      body: issuerAcquirerForm(txnFile, settlementFile),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.error || `Analysis failed: ${res.status}`);
    }
    return res.json();
  },

  downloadReport: (
    txnFile: File,
    settlementFile: File | null | undefined,
    reasons: Record<string, string>
  ) => {
    const fd = issuerAcquirerForm(txnFile, settlementFile);
    fd.append("reasons", JSON.stringify(reasons));
    return downloadBlob(
      "/api/issuer-acquirer/report",
      { method: "POST", body: fd },
      "Issuer_Acquirer_Reconciliation.xlsx"
    );
  },
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

/** A member code seen in real transactions that has no mapping, so every MID
 *  under it resolves to "No Aggregator". */
export type UnmappedCode = {
  member_code: string;
  txn_count: number;
  sample_mid: string | null;
  sample_merchant: string | null;
  last_seen: string | null;
};

export const partnerMappingsApi = {
  list: (bucket?: "aggregator" | "bank_wallet") =>
    request<PartnerMapping[]>(`/partner-mappings${bucket ? `?bucket=${bucket}` : ""}`),

  unmapped: () => request<UnmappedCode[]>("/partner-mappings/unmapped"),

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
