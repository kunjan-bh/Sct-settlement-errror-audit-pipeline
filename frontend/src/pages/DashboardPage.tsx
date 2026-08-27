import { useEffect, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { FiDownload, FiCheckCircle, FiTrash2, FiRefreshCw } from "react-icons/fi";
import { batchesApi, isIssueDecided, type BatchWithDashboard, type IssueStatusValue, type MidOverride, type PartnerSummary, type Issue } from "../lib/api";
import { cacheGet, cacheSet, cacheInvalidate } from "../lib/cache";
import StatCard from "../components/StatCard";
import SendSummaryOverlay from "../components/SendSummaryOverlay";
import IssueTable, { type FlatIssueRow } from "../components/IssueTable";
import ErrorClassification from "../components/ErrorClassification";

type TabKey = "solve" | "errors";

export default function DashboardPage() {
  const { batchId } = useParams<{ batchId: string }>();
  const id = Number(batchId);
  const navigate = useNavigate();

  const [data, setData] = useState<BatchWithDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [notesSaving, setNotesSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [showFinishConfirm, setShowFinishConfirm] = useState(false);
  const [unresolvedCount, setUnresolvedCount] = useState(0);
  // Shown once the batch is finished, so the summary email is composed from
  // the final state rather than from whatever was on screen mid-edit.
  const [showSendSummary, setShowSendSummary] = useState(false);
  const [tab, setTab] = useState<TabKey>("solve");

  const cacheKey = `batch:${id}`;

  const load = useCallback(async (opts?: { force?: boolean }) => {
    if (!opts?.force) {
      const cached = cacheGet<BatchWithDashboard>(cacheKey);
      if (cached) {
        setData(cached);
        setNotes(cached.batch.notes ?? "");
        setLoading(false);
        return;
      }
    }
    try {
      const result = await batchesApi.get(id);
      cacheSet(cacheKey, result);
      setData(result);
      setNotes(result.batch.notes ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load batch");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, cacheKey]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const handleUpdateIssue = async (issueId: number, patch: { status?: IssueStatusValue; comment?: string; mid_overrides?: Record<string, MidOverride> }) => {
    await batchesApi.updateIssue(id, issueId, patch);
    await load({ force: true });
  };

  const handleNotesBlur = async () => {
    if (!data || notes === (data.batch.notes ?? "")) return;
    setNotesSaving(true);
    try {
      await batchesApi.updateNotes(id, notes);
      cacheInvalidate(cacheKey);
    } finally {
      setNotesSaving(false);
    }
  };

  const executeFinishBatch = async () => {
    setShowFinishConfirm(false);
    setFinishing(true);
    try {
      // The summary email body is built from the batch notes, so flush any
      // unsaved edit first -- finishing straight from a focused textarea would
      // otherwise mail the previously saved version.
      if (data && notes !== (data.batch.notes ?? "")) {
        await batchesApi.updateNotes(id, notes);
        cacheInvalidate(cacheKey);
      }
      await batchesApi.finish(id);
      await load({ force: true });
      window.location.href = batchesApi.getReportUrl(id);
      setShowSendSummary(true);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to finish batch");
    } finally {
      setFinishing(false);
    }
  };

  const handleFinishBatch = async () => {
    if (!data) return;
    const allIssues: Issue[] = [];
    data.dashboard.aggregator_summary.forEach(p => {
        allIssues.push(...p.issues_failed, ...p.issues_pending, ...p.issues_lo_progress);
    });
    data.dashboard.bank_summary.forEach(p => {
        allIssues.push(...p.issues_failed, ...p.issues_pending, ...p.issues_lo_progress);
    });
    allIssues.push(...data.dashboard.sct_summary.issues_failed, ...data.dashboard.sct_summary.issues_pending, ...data.dashboard.sct_summary.issues_lo_progress);

    // An excluded issue is a decision, not unfinished work -- counting it
    // here told ops "8 issues are not solved" on a batch they had fully
    // worked through.
    const unresolved = allIssues.filter(issue => !isIssueDecided(issue.status)).length;

    if (unresolved > 0) {
      setUnresolvedCount(unresolved);
      setShowFinishConfirm(true);
    } else {
      await executeFinishBatch();
    }
  };

  const handleDeleteBatch = async () => {
    const confirmation = prompt("To delete this batch and all its records, please type 'delete' to confirm:");
    if (confirmation === "delete") {
      setDeleting(true);
      try {
        await batchesApi.remove(id);
        cacheInvalidate(cacheKey);
        navigate("/");
      } catch (e) {
        alert(e instanceof Error ? e.message : "Failed to delete batch");
        setDeleting(false);
      }
    } else if (confirmation !== null) {
      alert("Verification failed. Batch deletion canceled.");
    }
  };

  if (loading) return <div className="max-w-6xl mx-auto px-8 py-16 text-neutral-400">Loading…</div>;
  if (error) return <div className="max-w-6xl mx-auto px-8 py-16 text-red-600">{error}</div>;
  if (!data) return null;

  const { batch, dashboard } = data;
  const isFinished = batch.status === "finished";

  const mapToFlatRows = (issues: Issue[], partnerName: string): FlatIssueRow[] => issues.map(issue => ({
    issueId: issue.id,
    partnerName,
    category: issue.category,
    count: issue.count,
    affectedMids: issue.affected_mids,
    status: issue.status,
    comment: issue.comment,
    mid_overrides: issue.mid_overrides || {},
    last_solved_comment: issue.last_solved_comment,
  }));

  const renderPartnerSummary = (partner: PartnerSummary) => {
    const failedRows = mapToFlatRows(partner.issues_failed, partner.partner_name);
    const pendingRows = mapToFlatRows(partner.issues_pending, partner.partner_name);
    const loProgressRows = mapToFlatRows(partner.issues_lo_progress, partner.partner_name);

    return (
      <div key={partner.partner_name} className="bg-white border border-neutral-200 rounded-lg p-5 space-y-5 shadow-sm">
        <div className="flex items-center justify-between border-b border-neutral-100 pb-3">
          <h3 className="text-lg font-semibold text-neutral-900">{partner.partner_name}</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium bg-red-100 text-red-700 px-2 py-0.5 rounded-full">{partner.failed} Failed</span>
            <span className="text-xs font-medium bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full">{partner.pending} Pending</span>
            <span className="text-xs font-medium bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full">{partner.lo_progress} Lo Progress</span>
          </div>
        </div>

        {failedRows.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-sm font-semibold text-neutral-800 uppercase tracking-wider">Failed Transactions</h4>
            <IssueTable rows={failedRows} onUpdateIssue={handleUpdateIssue} emptyMessage="" />
          </div>
        )}

        {pendingRows.length > 0 && (
          <div className="space-y-2">
            <h4 className="text-sm font-semibold text-neutral-800 uppercase tracking-wider">Pending Transactions</h4>
            <IssueTable rows={pendingRows} onUpdateIssue={handleUpdateIssue} emptyMessage="" />
          </div>
        )}

        {loProgressRows.length > 0 && (
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-sm font-semibold text-neutral-800 uppercase tracking-wider">Lo Progress Transactions</h4>
              <a
                href={batchesApi.getAggregatorReportUrl(id, partner.partner_name, "lo_progress")}
                download
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-blue-50 hover:bg-blue-100 text-blue-700 font-medium text-xs transition-colors cursor-pointer"
                title={`Download only Lo Progress records for ${partner.partner_name}`}
              >
                <FiDownload className="text-sm" />
                Download LO Excel
              </a>
            </div>
            <IssueTable rows={loProgressRows} onUpdateIssue={handleUpdateIssue} emptyMessage="" />
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="max-w-6xl mx-auto px-8 py-10 space-y-8 font-sans">
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b border-neutral-200 pb-6">
        <div>
          <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">{batch.name}</h1>
          <p className="text-neutral-500 text-sm mt-1">
            {new Date(batch.created_at).toLocaleString()} ·{" "}
            <span className={isFinished ? "text-emerald-600 font-semibold" : "text-amber-600 font-semibold"}>
              {isFinished ? "Finished" : "Open"}
            </span>
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleDeleteBatch}
            disabled={deleting}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded border border-red-200 hover:border-red-300 text-red-600 hover:bg-red-50 text-xs font-semibold transition-colors disabled:opacity-50 cursor-pointer"
          >
            <FiTrash2 className="text-sm" />
            {deleting ? "Deleting…" : "Delete Batch"}
          </button>

          {!isFinished ? (
            <button
              onClick={handleFinishBatch}
              disabled={finishing}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs transition-colors shadow-sm disabled:opacity-50 cursor-pointer"
            >
              <FiCheckCircle className="text-emerald-400 text-sm" />
              {finishing ? "Finishing & Export" : "Finish Batch & Export"}
            </button>
          ) : (
            <a
              href={batchesApi.getReportUrl(id)}
              download
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-emerald-700 hover:bg-emerald-800 text-white font-semibold text-xs transition-colors shadow-sm cursor-pointer"
            >
              <FiDownload className="text-sm" />
              Download Full Report
            </a>
          )}
        </div>
      </header>

      {/* Two views of the same batch: what ops still has to fix (Solve), and
          what actually broke and how often (Error Classification). */}
      <nav className="flex items-center gap-1 border-b border-neutral-200 -mt-2">
        {([
          { key: "solve", label: "Solve Batch" },
          { key: "errors", label: "Error Classification" },
        ] as { key: TabKey; label: string }[]).map((t) => (
          <button
            key={t.key}
            type="button"
            onClick={() => setTab(t.key)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors cursor-pointer ${
              tab === t.key
                ? "border-neutral-900 text-neutral-900"
                : "border-transparent text-neutral-500 hover:text-neutral-800"
            }`}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "errors" && <ErrorClassification batchId={id} />}

      {tab === "solve" && (
      <>
      <section className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-4">
        <StatCard label="Total Txns" value={dashboard.totals.total_transactions} />
        <StatCard label="Pending" value={dashboard.totals.pending} />
        <StatCard label="Failed" value={dashboard.totals.failed} />
        <StatCard label="Lo Progress" value={dashboard.totals.lo_progress} />
        <StatCard label="SCT Failed" value={dashboard.totals.transaction_failed} />
        <StatCard label="Success" value={dashboard.totals.success_issues} />
        <StatCard label="No Aggregator" value={dashboard.totals.no_aggregator} />
        <StatCard label="Aggregators" value={dashboard.totals.total_aggregators} />
      </section>

      {/* Failures a later reprocess already settled are kept out of the issue
          tables below -- but never silently. */}
      {dashboard.totals.retry_resolved > 0 && (
        <div className="flex items-start gap-2.5 border border-emerald-200 bg-emerald-50/60 rounded-lg px-4 py-3">
          <FiRefreshCw className="text-emerald-600 mt-0.5 shrink-0 text-sm" />
          <p className="text-xs text-emerald-900 leading-relaxed">
            <strong>{dashboard.totals.retry_resolved}</strong> failed settlement
            {dashboard.totals.retry_resolved === 1 ? " was" : "s were"} reprocessed and settled later, so
            {dashboard.totals.retry_resolved === 1 ? " it is" : " they are"} excluded from the issues below.{" "}
            <button
              type="button"
              onClick={() => setTab("errors")}
              className="underline font-semibold cursor-pointer hover:text-emerald-700"
            >
              See which ones
            </button>
          </p>
        </div>
      )}

      {dashboard.aggregator_summary.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-base font-semibold text-neutral-950 uppercase tracking-wider text-xs">Aggregator Summary</h2>
          <div className="space-y-6">
            {dashboard.aggregator_summary.map(renderPartnerSummary)}
          </div>
        </section>
      )}

      {dashboard.bank_summary.length > 0 && (
        <section className="space-y-4">
          <h2 className="text-base font-semibold text-neutral-950 uppercase tracking-wider text-xs">Bank / Wallet Summary</h2>
          <div className="space-y-6">
            {dashboard.bank_summary.map(renderPartnerSummary)}
          </div>
        </section>
      )}

      {dashboard.sct_summary.total_issues > 0 && (
        <section className="space-y-4">
          <h2 className="text-base font-semibold text-neutral-950 uppercase tracking-wider text-xs">SCT Summary</h2>
          <div className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm space-y-5">
            {dashboard.sct_summary.issues_failed.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-neutral-800 uppercase tracking-wider">Failed</h4>
                <IssueTable rows={mapToFlatRows(dashboard.sct_summary.issues_failed, "SCT")} onUpdateIssue={handleUpdateIssue} emptyMessage="" />
              </div>
            )}
            {dashboard.sct_summary.issues_pending.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-neutral-800 uppercase tracking-wider">Pending</h4>
                <IssueTable rows={mapToFlatRows(dashboard.sct_summary.issues_pending, "SCT")} onUpdateIssue={handleUpdateIssue} emptyMessage="" />
              </div>
            )}
            {dashboard.sct_summary.issues_lo_progress.length > 0 && (
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-neutral-800 uppercase tracking-wider">Lo Progress</h4>
                <IssueTable rows={mapToFlatRows(dashboard.sct_summary.issues_lo_progress, "SCT")} onUpdateIssue={handleUpdateIssue} emptyMessage="" />
              </div>
            )}
          </div>
        </section>
      )}

      <section className="space-y-2 border-t border-neutral-200 pt-6">
        <h2 className="text-base font-semibold text-neutral-950 uppercase tracking-wider text-xs">Batch Notes</h2>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={handleNotesBlur}
          placeholder="e.g. Today's failures mostly due to NIC ASIA wallet validation."
          className="w-full border border-neutral-300 rounded px-3 py-2 text-sm h-24 resize-none bg-white focus:ring-1 focus:ring-neutral-950 focus:border-neutral-950 focus:outline-none"
        />
        {notesSaving && <p className="text-xs text-neutral-400">Saving…</p>}
      </section>
      </>
      )}

      {/* Minimalist Confirmation Modal */}
      {showFinishConfirm && (
        <div className="fixed inset-0 bg-neutral-950/20 backdrop-blur-[1px] flex items-center justify-center z-50 p-4">
          <div className="bg-white border border-neutral-200 rounded-lg shadow-xl max-w-sm w-full p-6 space-y-4">
            <div>
              <h3 className="text-sm font-semibold text-neutral-900 uppercase tracking-wider">Unresolved Issues</h3>
              <p className="text-xs text-neutral-500 mt-2 leading-relaxed">
                There are still <strong className="text-neutral-950 font-semibold">{unresolvedCount} issues</strong> that are not solved. Finalizing will lock this batch and export all remaining items as pending.
              </p>
            </div>
            <div className="flex items-center justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => setShowFinishConfirm(false)}
                className="px-3.5 py-1.5 border border-neutral-200 rounded text-neutral-600 hover:text-neutral-800 hover:bg-neutral-50 text-xs font-medium cursor-pointer transition-colors"
              >
                Go Back
              </button>
              <button
                type="button"
                onClick={executeFinishBatch}
                disabled={finishing}
                className="px-3.5 py-1.5 bg-neutral-950 text-white rounded hover:bg-neutral-800 text-xs font-medium cursor-pointer transition-colors disabled:opacity-50"
              >
                Proceed & Export
              </button>
            </div>
          </div>
        </div>
      )}

      {showSendSummary && (
        <SendSummaryOverlay batchId={id} onClose={() => setShowSendSummary(false)} />
      )}
    </div>
  );
}
