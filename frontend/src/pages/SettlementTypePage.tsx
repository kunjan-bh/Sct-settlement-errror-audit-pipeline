import { useEffect, useMemo, useRef, useState } from "react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  PieChart,
  Pie,
  Cell,
} from "recharts";
import { FiUpload, FiFileText, FiDownload, FiLink, FiX } from "react-icons/fi";
import { settlementTypeApi, type SettlementTypeData, type AdhocSettlementTypeData, type EntityType } from "../lib/api";
import { cacheGet, cacheSet } from "../lib/cache";
import StatCard from "../components/StatCard";

/**
 * Settlement Type Report: the success-side counterpart to Analytics. Of
 * every transaction that actually settled, how did it settle -- Real Time /
 * System Default / On Call -- broken down by aggregator/bank/wallet/SCT.
 * Same date-range-over-existing-batches scope as Analytics; no upload of
 * its own (see settlementTypeApi.get / settlement_type_service.py).
 *
 * Colors are deliberately distinct from Analytics' red/amber/blue "what
 * broke" palette -- this page is about "how", not "what broke", so it reads
 * as its own thing at a glance.
 */

const METHOD_COLORS = {
  real_time: "#16a34a",
  system_default: "#2563eb",
  on_call: "#9333ea",
  unknown: "#a8a29e",
};

const METHOD_LABELS: Record<keyof typeof METHOD_COLORS, string> = {
  real_time: "Real Time",
  system_default: "System Default",
  on_call: "On Call",
  unknown: "Unknown",
};

const ENTITY_TYPE_LABEL: Record<EntityType, string> = {
  aggregator: "Aggregator",
  bank_wallet: "Bank / Wallet",
  sct: "SCT",
  unmapped: "Unmapped",
};

const nf = new Intl.NumberFormat();
/** Settled amounts are NPR; two decimals so a report figure can be tied back
 * to the source file cell exactly rather than to a rounded version of it. */
const amtFmt = new Intl.NumberFormat(undefined, {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Renders an em dash rather than "NaN" when the figure is absent. Reachable
 * whenever the page is holding a response fetched before the backend started
 * sending `amount` -- the in-memory cache and any already-analyzed file both
 * survive a hot reload of this component, so the guard is not theoretical.
 */
function fmtAmount(value: number | undefined | null): string {
  return typeof value === "number" && Number.isFinite(value) ? amtFmt.format(value) : "—";
}

function toDateStr(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function startOfWeek(d: Date): Date {
  const day = d.getDay(); // 0 = Sunday
  const diff = (day === 0 ? -6 : 1) - day; // back to Monday
  const monday = new Date(d);
  monday.setDate(d.getDate() + diff);
  return monday;
}

type Preset = "today" | "week" | "month" | "7d" | "30d";

function presetRange(preset: Preset): { from: string; to: string } {
  const today = new Date();
  const to = toDateStr(today);
  if (preset === "today") return { from: to, to };
  if (preset === "week") return { from: toDateStr(startOfWeek(today)), to };
  if (preset === "month") {
    const first = new Date(today.getFullYear(), today.getMonth(), 1);
    return { from: toDateStr(first), to };
  }
  const back = new Date(today);
  back.setDate(back.getDate() - (preset === "7d" ? 6 : 29));
  return { from: toDateStr(back), to };
}

const PRESETS: { key: Preset; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "week", label: "This Week" },
  { key: "month", label: "This Month" },
  { key: "7d", label: "Last 7 Days" },
  { key: "30d", label: "Last 30 Days" },
];

const cacheKeyFor = (from: string, to: string) => `settlement-type:${from}:${to}`;

export default function SettlementTypePage() {
  const initial = useMemo(() => presetRange("30d"), []);
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);

  const cached = cacheGet<SettlementTypeData>(cacheKeyFor(from, to));
  const [data, setData] = useState<SettlementTypeData | null>(cached ?? null);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const handleDownloadReport = async () => {
    setDownloading(true);
    try {
      await settlementTypeApi.downloadReport(from, to);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to download report");
    } finally {
      setDownloading(false);
    }
  };

  useEffect(() => {
    const alreadyCached = cacheGet<SettlementTypeData>(cacheKeyFor(from, to));
    if (alreadyCached) {
      setData(alreadyCached);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    settlementTypeApi
      .get(from, to)
      .then((d) => {
        if (cancelled) return;
        cacheSet(cacheKeyFor(from, to), d);
        setData(d);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [from, to]);

  const donutData = useMemo(() => {
    if (!data) return [];
    const m = data.method_breakdown;
    return (Object.keys(METHOD_COLORS) as (keyof typeof METHOD_COLORS)[])
      .map((key) => ({ name: METHOD_LABELS[key], value: m[key], color: METHOD_COLORS[key] }))
      .filter((d) => d.value > 0);
  }, [data]);

  return (
    <div className="max-w-6xl mx-auto px-8 py-10 space-y-8 font-sans">
      <header className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">
            Settlement Type Report
          </h1>
          <p className="text-neutral-500 text-sm mt-1 max-w-2xl">
            Of everything that settled successfully in the selected range, how it settled — Real
            Time, System Default, or On Call — by aggregator, bank/wallet, and SCT.
          </p>
        </div>
        {data && data.kpis.total_settled > 0 && (
          <button
            type="button"
            onClick={handleDownloadReport}
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
          <label className="flex items-center gap-2 text-sm text-neutral-600">
            From
            <input
              type="date"
              value={from}
              max={to}
              onChange={(e) => setFrom(e.target.value)}
              className="border border-neutral-300 rounded px-2 py-1 text-sm"
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-neutral-600">
            To
            <input
              type="date"
              value={to}
              min={from}
              max={toDateStr(new Date())}
              onChange={(e) => setTo(e.target.value)}
              className="border border-neutral-300 rounded px-2 py-1 text-sm"
            />
          </label>

          <div className="h-5 w-px bg-neutral-200 mx-1" />

          {PRESETS.map((p) => (
            <button
              key={p.key}
              type="button"
              onClick={() => {
                const r = presetRange(p.key);
                setFrom(r.from);
                setTo(r.to);
              }}
              className="px-3 py-1.5 rounded-md text-xs font-medium text-neutral-600 border border-neutral-200 hover:bg-neutral-100 transition-colors cursor-pointer"
            >
              {p.label}
            </button>
          ))}
        </div>
      </section>

      {loading && <p className="text-neutral-400 text-sm py-10">Loading settlement type report…</p>}
      {error && <p className="text-red-600 text-sm py-10">{error}</p>}

      {data && !loading && !error && (
        <>
          {data.kpis.total_settled === 0 ? (
            <div className="bg-white border border-neutral-200 rounded-lg p-10 text-center shadow-sm">
              <p className="text-neutral-800 font-medium">No successful settlements in this range</p>
              <p className="text-neutral-400 text-sm mt-1">Try a wider date range.</p>
            </div>
          ) : (
            <>
              <section className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <StatCard label="Batches Included" value={data.kpis.batches_included} />
                <StatCard label="Total Settled" value={data.kpis.total_settled} />
                <StatCard label="Real Time" value={data.kpis.real_time} />
                <StatCard label="System Default" value={data.kpis.system_default} />
                <StatCard label="On Call" value={data.kpis.on_call} />
              </section>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                  <h2 className="text-sm font-semibold text-neutral-900 mb-4">
                    Settlement Method Split
                  </h2>
                  <ResponsiveContainer width="100%" height={300}>
                    <PieChart>
                      <Pie
                        data={donutData}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={70}
                        outerRadius={120}
                        paddingAngle={2}
                      >
                        {donutData.map((entry) => (
                          <Cell key={entry.name} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(value, name) => [nf.format(Number(value)), name]} />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                </section>

                <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                  <h2 className="text-sm font-semibold text-neutral-900 mb-4">
                    Settlement Method by Entity
                  </h2>
                  <ResponsiveContainer
                    width="100%"
                    height={Math.max(300, data.entities.length * 32)}
                  >
                    <BarChart data={data.entities} layout="vertical" margin={{ left: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e5e5" />
                      <XAxis type="number" tick={{ fontSize: 12 }} allowDecimals={false} />
                      <YAxis type="category" dataKey="entity" tick={{ fontSize: 11 }} width={140} />
                      <Tooltip formatter={(value) => nf.format(Number(value))} />
                      <Legend />
                      <Bar
                        dataKey="real_time"
                        stackId="s"
                        name="Real Time"
                        fill={METHOD_COLORS.real_time}
                      />
                      <Bar
                        dataKey="system_default"
                        stackId="s"
                        name="System Default"
                        fill={METHOD_COLORS.system_default}
                      />
                      <Bar dataKey="on_call" stackId="s" name="On Call" fill={METHOD_COLORS.on_call} />
                      <Bar
                        dataKey="unknown"
                        stackId="s"
                        name="Unknown"
                        fill={METHOD_COLORS.unknown}
                        radius={[0, 4, 4, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </section>
              </div>

              <section className="bg-white border border-neutral-200 rounded-lg shadow-sm overflow-hidden">
                <h2 className="text-sm font-semibold text-neutral-900 px-5 pt-5 pb-3">
                  Entity Breakdown
                </h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-y border-neutral-200 bg-neutral-50 text-neutral-500 text-xs uppercase tracking-wide">
                        <th className="text-left px-5 py-2 font-medium">Entity</th>
                        <th className="text-left px-3 py-2 font-medium">Type</th>
                        <th className="text-right px-3 py-2 font-medium">Real Time</th>
                        <th className="text-right px-3 py-2 font-medium">System Default</th>
                        <th className="text-right px-3 py-2 font-medium">On Call</th>
                        <th className="text-right px-3 py-2 font-medium">Unknown</th>
                        <th className="text-right px-3 py-2 font-medium">Total</th>
                        <th className="text-right px-5 py-2 font-medium">Total Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.entities.map((e, i) => (
                        <tr
                          key={e.entity}
                          className={`border-b border-neutral-100 ${i % 2 === 1 ? "bg-neutral-50/50" : ""}`}
                        >
                          <td className="px-5 py-2 text-neutral-800 font-medium">{e.entity}</td>
                          <td className="px-3 py-2 text-neutral-500 text-xs">
                            {ENTITY_TYPE_LABEL[e.entity_type]}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                            {nf.format(e.real_time)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                            {nf.format(e.system_default)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                            {nf.format(e.on_call)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-400">
                            {nf.format(e.unknown)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums font-semibold text-neutral-900">
                            {nf.format(e.total)}
                          </td>
                          <td className="px-5 py-2 text-right tabular-nums font-semibold text-neutral-900">
                            {fmtAmount(e.amount)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </>
      )}

      <DocumentAnalysisSection />
    </div>
  );
}

/**
 * Document Analysis: the same Settlement Type breakdown, but for one file
 * you upload right here instead of a date range over existing batches.
 * Nothing is persisted -- no Batch/Transaction row is created, and the file
 * is deleted on the backend right after it's classified. For a one-off file
 * (a sample, a standalone reconciliation) you want to check without adding
 * it to batch history.
 */
function DocumentAnalysisSection() {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const txnInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  // Optional: that day's Transaction List. It is only ever sent with a
  // report download -- the on-page analysis is a settlement-side summary and
  // has no MID-level detail for a transaction to sit beside.
  const [txnFile, setTxnFile] = useState<File | null>(null);
  const [data, setData] = useState<AdhocSettlementTypeData | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  const handleFileChange = (f: File | null) => {
    setFile(f);
    setData(null);
    setError(null);
  };

  const handleAnalyze = async () => {
    if (!file) return;
    setAnalyzing(true);
    setError(null);
    try {
      const result = await settlementTypeApi.analyzeDocument(file);
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to analyze file");
    } finally {
      setAnalyzing(false);
    }
  };

  const handleReset = () => {
    setFile(null);
    setTxnFile(null);
    setData(null);
    setError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (txnInputRef.current) txnInputRef.current.value = "";
  };

  const handleDownloadReport = async () => {
    if (!file) return;
    setDownloading(true);
    try {
      await settlementTypeApi.downloadDocumentReport(file, txnFile);
    } catch (e) {
      alert(e instanceof Error ? e.message : "Failed to download report");
    } finally {
      setDownloading(false);
    }
  };

  const donutData = useMemo(() => {
    if (!data) return [];
    const m = data.method_breakdown;
    return (Object.keys(METHOD_COLORS) as (keyof typeof METHOD_COLORS)[])
      .map((key) => ({ name: METHOD_LABELS[key], value: m[key], color: METHOD_COLORS[key] }))
      .filter((d) => d.value > 0);
  }, [data]);

  return (
    <section className="space-y-6 border-t border-neutral-200 pt-8">
      <header>
        <h2 className="text-xl font-semibold text-neutral-900 tracking-tight">Document Analysis</h2>
        <p className="text-neutral-500 text-sm mt-1 max-w-2xl leading-relaxed">
          Upload a separate settlement file to analyze it on its own — Real Time / System Default /
          On Call, by entity. This runs against the same classification rules and partner mappings,
          but nothing is saved: no batch is created and the file isn't added to batch history.
        </p>
        <p className="text-neutral-500 text-sm mt-2 max-w-2xl leading-relaxed">
          Optionally add the same day's <span className="font-medium text-neutral-700">transaction
          file</span> (.csv or Excel). The downloaded report then pairs every settled MID with the
          transaction it settled, entity by entity — settlements it can't trace are left as blank
          rows rather than guessed. The on-page figures below are the same either way.
        </p>
      </header>

      <div className="bg-white border border-neutral-200 rounded-lg p-4 shadow-sm">
        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx,.xls"
            onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
            className="hidden"
            id="adhoc-file-input"
          />
          <label
            htmlFor="adhoc-file-input"
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded border border-neutral-300 hover:border-neutral-400 text-neutral-700 text-xs font-semibold cursor-pointer transition-colors"
          >
            <FiFileText className="text-sm" />
            {file ? file.name : "Choose settlement file…"}
          </label>

          <input
            ref={txnInputRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={(e) => setTxnFile(e.target.files?.[0] ?? null)}
            className="hidden"
            id="adhoc-txn-input"
          />
          <label
            htmlFor="adhoc-txn-input"
            className={`inline-flex items-center gap-1.5 px-3.5 py-2 rounded border border-dashed text-xs font-semibold cursor-pointer transition-colors ${
              txnFile
                ? "border-emerald-400 bg-emerald-50 text-emerald-800"
                : "border-neutral-300 hover:border-neutral-400 text-neutral-500"
            }`}
          >
            <FiLink className="text-sm" />
            {txnFile ? txnFile.name : "Transaction file (optional)…"}
          </label>

          {txnFile && (
            <button
              type="button"
              onClick={() => {
                setTxnFile(null);
                if (txnInputRef.current) txnInputRef.current.value = "";
              }}
              title="Remove transaction file"
              className="px-2 py-1 text-xs font-medium text-neutral-400 hover:text-neutral-700 cursor-pointer"
            >
              <FiX />
            </button>
          )}

          <button
            type="button"
            onClick={handleAnalyze}
            disabled={!file || analyzing}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs transition-colors shadow-sm disabled:opacity-50 cursor-pointer"
          >
            <FiUpload className="text-sm" />
            {analyzing ? "Analyzing…" : "Analyze"}
          </button>

          {data && (
            <button
              type="button"
              onClick={handleDownloadReport}
              disabled={downloading}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded border border-neutral-300 hover:border-neutral-400 text-neutral-700 text-xs font-semibold transition-colors disabled:opacity-50 cursor-pointer"
            >
              <FiDownload className="text-sm" />
              {downloading ? "Preparing…" : "Download Report"}
            </button>
          )}

          {(file || data) && (
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
      </div>

      {data && (
        <>
          {data.kpis.total_settled === 0 ? (
            <div className="bg-white border border-neutral-200 rounded-lg p-10 text-center shadow-sm">
              <p className="text-neutral-800 font-medium">No successful settlements in this file</p>
              <p className="text-neutral-400 text-sm mt-1">
                {nf.format(data.row_count)} row{data.row_count === 1 ? "" : "s"} read, none marked
                success.
              </p>
            </div>
          ) : (
            <>
              <section className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <StatCard label="Rows Read" value={data.row_count} />
                <StatCard label="Total Settled" value={data.kpis.total_settled} />
                <StatCard label="Real Time" value={data.kpis.real_time} />
                <StatCard label="System Default" value={data.kpis.system_default} />
                <StatCard label="On Call" value={data.kpis.on_call} />
              </section>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                  <h3 className="text-sm font-semibold text-neutral-900 mb-4">
                    Settlement Method Split
                  </h3>
                  <ResponsiveContainer width="100%" height={300}>
                    <PieChart>
                      <Pie
                        data={donutData}
                        dataKey="value"
                        nameKey="name"
                        innerRadius={70}
                        outerRadius={120}
                        paddingAngle={2}
                      >
                        {donutData.map((entry) => (
                          <Cell key={entry.name} fill={entry.color} />
                        ))}
                      </Pie>
                      <Tooltip formatter={(value, name) => [nf.format(Number(value)), name]} />
                      <Legend />
                    </PieChart>
                  </ResponsiveContainer>
                </section>

                <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                  <h3 className="text-sm font-semibold text-neutral-900 mb-4">
                    Settlement Method by Entity
                  </h3>
                  <ResponsiveContainer
                    width="100%"
                    height={Math.max(300, data.entities.length * 32)}
                  >
                    <BarChart data={data.entities} layout="vertical" margin={{ left: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e5e5" />
                      <XAxis type="number" tick={{ fontSize: 12 }} allowDecimals={false} />
                      <YAxis type="category" dataKey="entity" tick={{ fontSize: 11 }} width={140} />
                      <Tooltip formatter={(value) => nf.format(Number(value))} />
                      <Legend />
                      <Bar dataKey="real_time" stackId="s" name="Real Time" fill={METHOD_COLORS.real_time} />
                      <Bar
                        dataKey="system_default"
                        stackId="s"
                        name="System Default"
                        fill={METHOD_COLORS.system_default}
                      />
                      <Bar dataKey="on_call" stackId="s" name="On Call" fill={METHOD_COLORS.on_call} />
                      <Bar
                        dataKey="unknown"
                        stackId="s"
                        name="Unknown"
                        fill={METHOD_COLORS.unknown}
                        radius={[0, 4, 4, 0]}
                      />
                    </BarChart>
                  </ResponsiveContainer>
                </section>
              </div>

              <section className="bg-white border border-neutral-200 rounded-lg shadow-sm overflow-hidden">
                <h3 className="text-sm font-semibold text-neutral-900 px-5 pt-5 pb-3">
                  Entity Breakdown — {data.file_name}
                </h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-y border-neutral-200 bg-neutral-50 text-neutral-500 text-xs uppercase tracking-wide">
                        <th className="text-left px-5 py-2 font-medium">Entity</th>
                        <th className="text-left px-3 py-2 font-medium">Type</th>
                        <th className="text-right px-3 py-2 font-medium">Real Time</th>
                        <th className="text-right px-3 py-2 font-medium">System Default</th>
                        <th className="text-right px-3 py-2 font-medium">On Call</th>
                        <th className="text-right px-3 py-2 font-medium">Unknown</th>
                        <th className="text-right px-3 py-2 font-medium">Total</th>
                        <th className="text-right px-5 py-2 font-medium">Total Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.entities.map((e, i) => (
                        <tr
                          key={e.entity}
                          className={`border-b border-neutral-100 ${i % 2 === 1 ? "bg-neutral-50/50" : ""}`}
                        >
                          <td className="px-5 py-2 text-neutral-800 font-medium">{e.entity}</td>
                          <td className="px-3 py-2 text-neutral-500 text-xs">
                            {ENTITY_TYPE_LABEL[e.entity_type]}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                            {nf.format(e.real_time)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                            {nf.format(e.system_default)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-700">
                            {nf.format(e.on_call)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums text-neutral-400">
                            {nf.format(e.unknown)}
                          </td>
                          <td className="px-3 py-2 text-right tabular-nums font-semibold text-neutral-900">
                            {nf.format(e.total)}
                          </td>
                          <td className="px-5 py-2 text-right tabular-nums font-semibold text-neutral-900">
                            {fmtAmount(e.amount)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            </>
          )}
        </>
      )}
    </section>
  );
}
