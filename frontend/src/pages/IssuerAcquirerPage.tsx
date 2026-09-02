import { useMemo, useRef, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { FiUpload, FiFileText, FiLink, FiDownload, FiX } from "react-icons/fi";
import { issuerAcquirerApi, type IssuerAcquirerData } from "../lib/api";
import StatCard from "../components/StatCard";

/**
 * Issuer / Acquirer reconciliation.
 *
 * Every transaction debits an issuer and pays an acquirer, so inside the
 * transaction file the two sides always balance to the rupee. The gap worth
 * looking at is against settlement: what the switch transacted versus what was
 * actually paid out. A shortfall is usually pending settlement rather than a
 * break, which is why each acquirer row takes a free-text reason -- the number
 * cannot tell you which, and only ops knows.
 *
 * Reasons live in component state and travel with the report request. They are
 * not persisted: this page is a one-off reconciliation of two uploaded files,
 * like Document Analysis, and nothing about the files is kept either.
 */

const ISSUING_COLOR = "#2f5597";
const TRANSACTED_COLOR = "#2f5597";
const SETTLED_COLOR = "#16a34a";

const nf = new Intl.NumberFormat();
const amt = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });

export default function IssuerAcquirerPage() {
  const txnRef = useRef<HTMLInputElement>(null);
  const settleRef = useRef<HTMLInputElement>(null);
  const [txnFile, setTxnFile] = useState<File | null>(null);
  const [settleFile, setSettleFile] = useState<File | null>(null);
  const [data, setData] = useState<IssuerAcquirerData | null>(null);
  const [reasons, setReasons] = useState<Record<string, string>>({});
  const [analyzing, setAnalyzing] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAnalyze = async () => {
    if (!txnFile) return;
    setAnalyzing(true);
    setError(null);
    try {
      setData(await issuerAcquirerApi.analyze(txnFile, settleFile));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to analyze");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleDownload = async () => {
    if (!txnFile) return;
    setDownloading(true);
    try {
      await issuerAcquirerApi.downloadReport(txnFile, settleFile, reasons);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to download report");
    } finally {
      setDownloading(false);
    }
  };

  const handleReset = () => {
    setTxnFile(null);
    setSettleFile(null);
    setData(null);
    setReasons({});
    setError(null);
    if (txnRef.current) txnRef.current.value = "";
    if (settleRef.current) settleRef.current.value = "";
  };

  const issuingChart = useMemo(
    () =>
      (data?.issuing ?? []).slice(0, 10).map((r) => ({
        name: r.name.length > 22 ? `${r.name.slice(0, 21)}…` : r.name,
        amount: r.txn_amount,
      })),
    [data]
  );

  const acquiringChart = useMemo(
    () =>
      (data?.acquiring ?? []).slice(0, 10).map((r) => ({
        name: r.name.length > 22 ? `${r.name.slice(0, 21)}…` : r.name,
        transacted: r.txn_amount,
        settled: r.settled_amount,
      })),
    [data]
  );

  const t = data?.totals;

  return (
    <div className="max-w-6xl mx-auto px-8 py-10 space-y-8 font-sans">
      <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">
            Issuer &amp; Acquirer
          </h1>
          <p className="text-neutral-500 text-sm mt-1 max-w-3xl leading-relaxed">
            Upload a day's transactions and its settlement file. Every transaction debits an issuer
            and pays an acquirer, so those two always balance — the gap worth reading is what was
            transacted against what actually settled. Where they differ, write the reason.
          </p>
        </div>
        {data && (
          <button
            type="button"
            onClick={handleDownload}
            disabled={downloading}
            className="shrink-0 inline-flex items-center gap-1.5 px-3.5 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs transition-colors shadow-sm disabled:opacity-50 cursor-pointer"
          >
            <FiDownload className="text-sm" />
            {downloading ? "Preparing…" : "Download Report"}
          </button>
        )}
      </header>

      <section className="bg-white border border-neutral-200 rounded-lg p-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={txnRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            className="hidden"
            id="ia-txn"
            onChange={(e) => {
              setTxnFile(e.target.files?.[0] ?? null);
              setData(null);
            }}
          />
          <label
            htmlFor="ia-txn"
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded border border-neutral-300 hover:border-neutral-400 text-neutral-700 text-xs font-semibold cursor-pointer transition-colors"
          >
            <FiFileText className="text-sm" />
            {txnFile ? txnFile.name : "Choose transaction file…"}
          </label>

          <input
            ref={settleRef}
            type="file"
            accept=".xlsx,.xls"
            className="hidden"
            id="ia-settle"
            onChange={(e) => {
              setSettleFile(e.target.files?.[0] ?? null);
              setData(null);
            }}
          />
          <label
            htmlFor="ia-settle"
            className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded border border-dashed text-xs font-semibold cursor-pointer transition-colors ${
              settleFile
                ? "border-emerald-400 bg-emerald-50 text-emerald-800"
                : "border-neutral-300 hover:border-neutral-400 text-neutral-500"
            }`}
          >
            <FiLink className="text-sm" />
            {settleFile ? settleFile.name : "Settlement file (optional)…"}
          </label>
          {settleFile && (
            <button
              type="button"
              title="Remove settlement file"
              onClick={() => {
                setSettleFile(null);
                setData(null);
                if (settleRef.current) settleRef.current.value = "";
              }}
              className="px-2 py-1 text-xs text-neutral-400 hover:text-neutral-700 cursor-pointer"
            >
              <FiX />
            </button>
          )}

          <button
            type="button"
            onClick={handleAnalyze}
            disabled={!txnFile || analyzing}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs transition-colors shadow-sm disabled:opacity-50 cursor-pointer"
          >
            <FiUpload className="text-sm" />
            {analyzing ? "Reconciling…" : "Reconcile"}
          </button>

          {(txnFile || data) && (
            <button
              type="button"
              onClick={handleReset}
              disabled={analyzing}
              className="px-3 py-1.5 text-xs font-medium text-neutral-500 hover:text-neutral-800 cursor-pointer disabled:opacity-50"
            >
              Clear
            </button>
          )}
        </div>
        {error && <p className="text-red-600 text-xs mt-3">{error}</p>}
        {!settleFile && (
          <p className="text-neutral-400 text-xs mt-3">
            Without a settlement file you still get both sides of the switch's own view — the
            settled columns and every variance stay empty.
          </p>
        )}
      </section>

      {data && t && (
        <>
          <section className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <StatCard label="Transactions" value={t.txn_rows} />
            <StatCard label="Transacted (NPR)" value={Math.round(t.txn_amount)} />
            <StatCard label="Settled (NPR)" value={Math.round(t.settled_amount)} />
            <StatCard label="Variance (NPR)" value={Math.round(t.variance_amount)} />
            <StatCard label="Settled %" value={t.settled_pct} />
          </section>

          {t.window && (
            <p className="text-neutral-500 text-xs -mt-4">
              Transactions cover {t.window}
              {data.files.settlement ? ` · settlement from ${data.files.settlement}` : ""}
            </p>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
              <h2 className="text-sm font-semibold text-neutral-900 mb-1">Issuing volume</h2>
              <p className="text-neutral-500 text-xs mb-4">
                Whose customers spent, by issuing bank or wallet.
              </p>
              <ResponsiveContainer width="100%" height={Math.max(260, issuingChart.length * 30)}>
                <BarChart data={issuingChart} layout="vertical" margin={{ left: 24, right: 24 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f1f1" />
                  <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => compact.format(v)} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={130} />
                  <Tooltip formatter={(v) => amt.format(Number(v))} />
                  <Bar dataKey="amount" name="Issued" fill={ISSUING_COLOR} radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </section>

            <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
              <h2 className="text-sm font-semibold text-neutral-900 mb-1">
                Acquiring — transacted vs settled
              </h2>
              <p className="text-neutral-500 text-xs mb-4">
                Who got paid. A shorter green bar is money still to settle.
              </p>
              <ResponsiveContainer width="100%" height={Math.max(260, acquiringChart.length * 30)}>
                <BarChart data={acquiringChart} layout="vertical" margin={{ left: 24, right: 24 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#f1f1f1" />
                  <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => compact.format(v)} />
                  <YAxis type="category" dataKey="name" tick={{ fontSize: 11 }} width={130} />
                  <Tooltip formatter={(v) => amt.format(Number(v))} />
                  <Legend />
                  <Bar dataKey="transacted" name="Transacted" fill={TRANSACTED_COLOR} />
                  <Bar dataKey="settled" name="Settled" fill={SETTLED_COLOR} />
                </BarChart>
              </ResponsiveContainer>
            </section>
          </div>

          <Table
            title="Issuing"
            subtitle="One row per issuing bank or wallet."
            head={["Issuer", "Transactions", "Amount", "% of total"]}
            rows={data.issuing.map((r) => [
              r.name,
              nf.format(r.txn_count),
              amt.format(r.txn_amount),
              `${r.share.toFixed(1)}%`,
            ])}
          />

          <section className="bg-white border border-neutral-200 rounded-lg shadow-sm overflow-hidden">
            <div className="px-5 pt-5 pb-3">
              <h2 className="text-sm font-semibold text-neutral-900">Acquiring</h2>
              <p className="text-neutral-500 text-xs mt-1">
                Positive variance is transacted but not yet settled. Write why it differs — the
                reason travels into the downloaded report.
              </p>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-y border-neutral-200 bg-neutral-50 text-neutral-500 text-xs uppercase tracking-wide">
                    <th className="text-left px-5 py-2 font-medium">Acquirer</th>
                    <th className="text-right px-3 py-2 font-medium">Transacted</th>
                    <th className="text-right px-3 py-2 font-medium">Settled</th>
                    <th className="text-right px-3 py-2 font-medium">Variance</th>
                    <th className="text-left px-5 py-2 font-medium w-72">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {data.acquiring.map((r, i) => (
                    <tr
                      key={r.name}
                      className={`border-b border-neutral-100 ${i % 2 === 1 ? "bg-neutral-50/50" : ""}`}
                    >
                      <td className="px-5 py-2 text-neutral-800 font-medium">{r.name}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                        {amt.format(r.txn_amount)}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                        {amt.format(r.settled_amount)}
                      </td>
                      <td
                        className={`px-3 py-2 text-right tabular-nums font-semibold ${
                          Math.abs(r.variance_amount) < 0.005
                            ? "text-neutral-400"
                            : "text-neutral-900"
                        }`}
                      >
                        {amt.format(r.variance_amount)}
                      </td>
                      <td className="px-5 py-2">
                        <input
                          value={reasons[r.name] ?? ""}
                          placeholder={
                            Math.abs(r.variance_amount) < 0.005 ? "—" : "why does it differ?"
                          }
                          onChange={(e) =>
                            setReasons((prev) => ({ ...prev, [r.name]: e.target.value }))
                          }
                          className="w-full border border-neutral-200 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-neutral-400"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function Table({
  title,
  subtitle,
  head,
  rows,
}: {
  title: string;
  subtitle: string;
  head: string[];
  rows: string[][];
}) {
  return (
    <section className="bg-white border border-neutral-200 rounded-lg shadow-sm overflow-hidden">
      <div className="px-5 pt-5 pb-3">
        <h2 className="text-sm font-semibold text-neutral-900">{title}</h2>
        <p className="text-neutral-500 text-xs mt-1">{subtitle}</p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-y border-neutral-200 bg-neutral-50 text-neutral-500 text-xs uppercase tracking-wide">
              {head.map((h, i) => (
                <th
                  key={h}
                  className={`${i === 0 ? "text-left px-5" : "text-right px-3"} py-2 font-medium`}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r[0]} className={`border-b border-neutral-100 ${i % 2 === 1 ? "bg-neutral-50/50" : ""}`}>
                {r.map((cell, j) => (
                  <td
                    key={j}
                    className={`${
                      j === 0
                        ? "text-left px-5 text-neutral-800 font-medium"
                        : "text-right px-3 tabular-nums text-neutral-700"
                    } py-2`}
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
