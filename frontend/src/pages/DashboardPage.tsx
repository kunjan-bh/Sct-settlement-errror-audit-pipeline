import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams } from "react-router-dom";
import { motion } from "framer-motion";
import { FiDownload, FiCheckCircle } from "react-icons/fi";
import { batchesApi, type BatchWithDashboard, type IssueStatusValue } from "../lib/api";
import StatCard from "../components/StatCard";
import IssueTable, { type FlatIssueRow } from "../components/IssueTable";

export default function DashboardPage() {
  const { batchId } = useParams<{ batchId: string }>();
  const id = Number(batchId);

  const [data, setData] = useState<BatchWithDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState("");
  const [notesSaving, setNotesSaving] = useState(false);
  const [finishing, setFinishing] = useState(false);

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

  const handleFinishBatch = async () => {
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
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="max-w-6xl mx-auto px-8 py-10 space-y-10"
    >
      <header className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-neutral-900">{batch.name}</h1>
          <p className="text-neutral-400 text-sm mt-1">
            {new Date(batch.created_at).toLocaleString()} ·{" "}
            <span className={isFinished ? "text-emerald-600 font-medium" : "text-amber-600 font-medium"}>
              {isFinished ? "Finished" : "Open"}
            </span>
          </p>
        </div>

        <div className="flex items-center gap-3">
          {!isFinished ? (
            <button
              onClick={handleFinishBatch}
              disabled={finishing}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-neutral-900 hover:bg-neutral-800 text-white font-medium text-sm transition-colors shadow-sm disabled:opacity-50 cursor-pointer"
            >
              <FiCheckCircle className="text-emerald-400 text-base" />
              {finishing ? "Finishing…" : "Finish Batch & Export Report"}
            </button>
          ) : (
            <a
              href={batchesApi.getReportUrl(id)}
              download
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg bg-emerald-700 hover:bg-emerald-800 text-white font-medium text-sm transition-colors shadow-sm cursor-pointer"
            >
              <FiDownload className="text-base" />
              Download Excel Report
            </a>
          )}
        </div>
      </header>

      <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Total Transactions" value={dashboard.totals.total_transactions} />
        <StatCard label="Pending" value={dashboard.totals.pending} />
        <StatCard label="Settlement Failed" value={dashboard.totals.settlement_failed} />
        <StatCard label="Transaction Failed (SCT)" value={dashboard.totals.transaction_failed} />
        <StatCard label="No Aggregator" value={dashboard.totals.no_aggregator} />
        <StatCard label="Total Aggregators" value={dashboard.totals.total_aggregators} />
      </section>

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-neutral-900">Aggregator Summary</h2>
          <span className="text-xs font-medium text-neutral-500 bg-neutral-100 px-2.5 py-1 rounded-full">
            {aggregatorRows.length} {aggregatorRows.length === 1 ? "issue" : "issues"}
          </span>
        </div>
        <IssueTable
          rows={aggregatorRows}
          onUpdateIssue={handleUpdateIssue}
          emptyMessage="No transactions resolved to an aggregator yet — import your coop member codes on the Partner Mapping page."
        />
      </section>

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-neutral-900">Bank / Wallet Summary</h2>
          <span className="text-xs font-medium text-neutral-500 bg-neutral-100 px-2.5 py-1 rounded-full">
            {bankRows.length} {bankRows.length === 1 ? "issue" : "issues"}
          </span>
        </div>
        <IssueTable
          rows={bankRows}
          onUpdateIssue={handleUpdateIssue}
          emptyMessage="No bank/wallet issues reported."
        />
      </section>

      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-semibold text-neutral-900">SCT Summary</h2>
          <span className="text-xs font-medium text-neutral-500 bg-neutral-100 px-2.5 py-1 rounded-full">
            {sctRows.length} {sctRows.length === 1 ? "issue" : "issues"}
          </span>
        </div>
        <IssueTable
          rows={sctRows}
          onUpdateIssue={handleUpdateIssue}
          emptyMessage="No SCT issues reported."
        />
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold text-neutral-900">Batch Notes</h2>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          onBlur={handleNotesBlur}
          placeholder="e.g. Today's failures mostly due to NIC ASIA wallet validation."
          className="w-full border border-neutral-300 rounded-lg px-4 py-3 text-sm h-28 resize-none bg-white focus:ring-1 focus:ring-neutral-400 focus:outline-none"
        />
        {notesSaving && <p className="text-xs text-neutral-400">Saving…</p>}
      </section>
    </motion.div>
  );
}
