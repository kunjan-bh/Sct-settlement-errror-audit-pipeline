import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { FiDownload, FiCheckCircle, FiTrash2 } from "react-icons/fi";
import { batchesApi, type BatchWithDashboard, type IssueStatusValue } from "../lib/api";
import StatCard from "../components/StatCard";
import IssueTable, { type FlatIssueRow } from "../components/IssueTable";

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

  const load = useCallback(async () => {
    try {
      const result = await batchesApi.get(id);
      setData(result);
      setNotes(result.batch.notes ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load batch");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setLoading(true);
    load();
  }, [load]);

  const handleUpdateIssue = async (issueId: number, patch: { status?: IssueStatusValue; comment?: string }) => {
    await batchesApi.updateIssue(id, issueId, patch);
    await load();
  };

  const handleNotesBlur = async () => {
    if (!data || notes === (data.batch.notes ?? "")) return;
    setNotesSaving(true);
    try {
      await batchesApi.updateNotes(id, notes);
    } finally {
      setNotesSaving(false);
    }
  };

  const executeFinishBatch = async () => {
    setShowFinishConfirm(false);
    setFinishing(true);
    try {
      await batchesApi.finish(id);
      await load();
      // Trigger report download
      window.location.href = batchesApi.getReportUrl(id);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to finish batch");
    } finally {
      setFinishing(false);
    }
  };

  const handleFinishBatch = async () => {
    const unresolved = [
      ...aggregatorRows,
      ...bankRows,
      ...sctRows
    ].filter(row => row.status !== "solved").length;

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
        navigate("/");
      } catch (e) {
        alert(e instanceof Error ? e.message : "Failed to delete batch");
        setDeleting(false);
      }
    } else if (confirmation !== null) {
      alert("Verification failed. Batch deletion canceled.");
    }
  };

  const aggregatorRows: FlatIssueRow[] = useMemo(() => {
    if (!data) return [];
    return data.dashboard.aggregator_summary.flatMap((partner) =>
      partner.issues.map((issue) => ({
        issueId: issue.id,
        partnerName: partner.partner_name,
        category: issue.category,
        count: issue.count,
        affectedMids: issue.affected_mids,
        status: issue.status,
        comment: issue.comment,
      }))
    );
  }, [data]);

  const bankRows: FlatIssueRow[] = useMemo(() => {
    if (!data) return [];
    return data.dashboard.bank_summary.flatMap((partner) =>
      partner.issues.map((issue) => ({
        issueId: issue.id,
        partnerName: partner.partner_name,
        category: issue.category,
        count: issue.count,
        affectedMids: issue.affected_mids,
        status: issue.status,
        comment: issue.comment,
      }))
    );
  }, [data]);

  const sctRows: FlatIssueRow[] = useMemo(() => {
    if (!data) return [];
    return data.dashboard.sct_summary.issues.map((issue) => ({
      issueId: issue.id,
      partnerName: "SCT",
      category: issue.category,
      count: issue.count,
      affectedMids: issue.affected_mids,
      status: issue.status,
      comment: issue.comment,
    }));
  }, [data]);

  if (loading) return <div className="max-w-6xl mx-auto px-8 py-16 text-neutral-400">Loading…</div>;
  if (error) return <div className="max-w-6xl mx-auto px-8 py-16 text-red-600">{error}</div>;
  if (!data) return null;

  const { batch, dashboard } = data;
  const isFinished = batch.status === "finished";

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
              {finishing ? "Finishing…" : "Finish Batch & Export"}
            </button>
          ) : (
            <a
              href={batchesApi.getReportUrl(id)}
              download
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-emerald-700 hover:bg-emerald-800 text-white font-semibold text-xs transition-colors shadow-sm cursor-pointer"
            >
              <FiDownload className="text-sm" />
              Download Excel
            </a>
          )}
        </div>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-6 gap-4">
        <StatCard label="Total Txns" value={dashboard.totals.total_transactions} />
        <StatCard label="Pending" value={dashboard.totals.pending} />
        <StatCard label="Settlement Failed" value={dashboard.totals.settlement_failed} />
        <StatCard label="SCT Failed" value={dashboard.totals.transaction_failed} />
        <StatCard label="No Aggregator" value={dashboard.totals.no_aggregator} />
        <StatCard label="Aggregators" value={dashboard.totals.total_aggregators} />
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-950 uppercase tracking-wider text-xs">Aggregator Summary</h2>
          <span className="text-xs font-medium text-neutral-500 bg-neutral-100 px-2 py-0.5 rounded-full">
            {aggregatorRows.length} {aggregatorRows.length === 1 ? "issue" : "issues"}
          </span>
        </div>
        <IssueTable
          rows={aggregatorRows}
          onUpdateIssue={handleUpdateIssue}
          emptyMessage="No transactions resolved to an aggregator yet — import your coop member codes on the Partner Mapping page."
        />
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-950 uppercase tracking-wider text-xs">Bank / Wallet Summary</h2>
          <span className="text-xs font-medium text-neutral-500 bg-neutral-100 px-2 py-0.5 rounded-full">
            {bankRows.length} {bankRows.length === 1 ? "issue" : "issues"}
          </span>
        </div>
        <IssueTable
          rows={bankRows}
          onUpdateIssue={handleUpdateIssue}
          emptyMessage="No bank/wallet issues reported."
        />
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-950 uppercase tracking-wider text-xs">SCT Summary</h2>
          <span className="text-xs font-medium text-neutral-500 bg-neutral-100 px-2 py-0.5 rounded-full">
            {sctRows.length} {sctRows.length === 1 ? "issue" : "issues"}
          </span>
        </div>
        <IssueTable
          rows={sctRows}
          onUpdateIssue={handleUpdateIssue}
          emptyMessage="No SCT issues reported."
        />
      </section>

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
    </div>
  );
}
