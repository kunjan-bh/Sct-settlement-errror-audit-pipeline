import { useCallback, useEffect, useRef, useState } from "react";
import {
  FiAlertTriangle, FiCheckCircle, FiClock, FiDownload, FiPlay, FiRefreshCw,
} from "react-icons/fi";
import { reconcileApi, type ReconJob, type ReconResult, type ReconRow } from "../lib/api";
import { localIso, localIsoDaysAgo } from "../lib/localdate";

/**
 * Reconciliation: does everything that came in go back out?
 *
 * Incoming is a QR payment; outgoing is the settlement that pays it to the
 * merchant. The page exists to show that the difference between the two is
 * fully accounted for -- and to name what is not.
 *
 * It runs as a job because matching a day takes about eighty seconds, and
 * shows which of five stages it is on rather than an anonymous spinner.
 */

const money = (n: number) =>
  `NPR ${n.toLocaleString("en-NP", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const short = (n: number) => `NPR ${Math.round(n).toLocaleString("en-NP")}`;

const BUCKETS: { key: string; label: string; tone: "red" | "amber" | "green" | "grey"; blurb: string }[] = [
  {
    key: "not_in_settlement_report", label: "Never raised for payment", tone: "red",
    blurb: "In the transaction report, but nothing was ever raised to pay it out.",
  },
  {
    key: "settlement_failed", label: "Settlement failed", tone: "red",
    blurb: "A settlement was raised and did not go through, and the merchant is still holding the money.",
  },
  {
    key: "awaiting_settlement", label: "Awaiting settlement", tone: "amber",
    blurb: "No settlement yet, but still within normal time.",
  },
  {
    key: "settled_later", label: "Settled later", tone: "green",
    blurb: "The settlement failed, but the merchant holds nothing — it went out on a retry or a later run.",
  },
];

const TONE: Record<string, string> = {
  red: "border-red-200 bg-red-50 text-red-900",
  amber: "border-amber-200 bg-amber-50 text-amber-900",
  green: "border-emerald-200 bg-emerald-50 text-emerald-900",
  grey: "border-neutral-200 bg-white text-neutral-900",
};

function Row({ label, value, strong = false, tone }: {
  label: string; value: string; strong?: boolean; tone?: string;
}) {
  return (
    <div className={`flex items-baseline justify-between gap-6 py-1.5 ${strong ? "font-semibold" : ""}`}>
      <span className={tone ?? "text-neutral-600"}>{label}</span>
      <span className={`tabular-nums ${tone ?? "text-neutral-900"}`}>{value}</span>
    </div>
  );
}

function Table({ rows }: { rows: ReconRow[] }) {
  if (!rows.length) return <p className="text-xs text-neutral-400 px-4 py-4">Nothing here.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-neutral-100 text-neutral-600">
            {["MID", "Aggregator", "CRRN", "Amount", "Payment date", "Settlement", "Why"].map((h) => (
              <th key={h} className="text-left font-semibold px-3 py-2 whitespace-nowrap">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, 300).map((r, i) => (
            <tr key={`${r.txn_id}-${i}`} className="border-t border-neutral-100 align-top">
              <td className="px-3 py-1.5 font-mono whitespace-nowrap">{r.mid}</td>
              <td className="px-3 py-1.5 whitespace-nowrap">{r.partner}</td>
              <td className="px-3 py-1.5 font-mono text-neutral-500 whitespace-nowrap">{r.crrn}</td>
              <td className="px-3 py-1.5 text-right tabular-nums whitespace-nowrap">{money(r.txn_amount)}</td>
              <td className="px-3 py-1.5 text-neutral-500 whitespace-nowrap">{r.txn_date_time.slice(0, 16)}</td>
              <td className="px-3 py-1.5 whitespace-nowrap">{r.settle_status ?? "—"}</td>
              <td className="px-3 py-1.5 text-neutral-600">{r.why}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length > 300 && (
        <p className="text-[11px] text-neutral-400 px-3 py-2">
          Showing the first 300 of {rows.length.toLocaleString()} — the full list is in the report.
        </p>
      )}
    </div>
  );
}

export default function ReconcilePage() {
  const yesterday = localIsoDaysAgo(1);
  const [from, setFrom] = useState(yesterday);
  const [to, setTo] = useState(yesterday);
  const [job, setJob] = useState<ReconJob | null>(null);
  const [data, setData] = useState<ReconResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openBucket, setOpenBucket] = useState<string>("");
  const timer = useRef<number | null>(null);

  const stop = () => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = null;
  };
  useEffect(() => stop, []);

  const run = useCallback(async () => {
    stop();
    setError(null);
    setData(null);
    setJob(null);
    try {
      const { job_id, steps } = await reconcileApi.start(from, to);
      setJob({
        id: job_id, steps, step: steps[0], step_index: 0,
        done: false, error: null, elapsed_seconds: 0,
      });
      timer.current = window.setInterval(async () => {
        try {
          const s = await reconcileApi.status(job_id);
          setJob(s);
          if (s.done) {
            stop();
            if (s.error) setError(s.error);
            else if (s.result) setData(s.result);
          }
        } catch (e) {
          stop();
          setError(e instanceof Error ? e.message : "Lost track of the job");
        }
      }, 1500);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start reconciliation");
    }
  }, [from, to]);

  const t = data?.totals;
  const explained = t
    ? t.settled_later_amount + t.settlement_failed_amount + t.awaiting_amount +
      t.not_in_report_amount + t.batch_merchant_amount
    : 0;
  const residual = t ? Math.round((t.difference - explained) * 100) / 100 : 0;

  return (
    <div className="max-w-7xl mx-auto px-8 py-10 space-y-6 font-sans">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">Reconciliation</h1>
        <p className="text-neutral-500 text-sm mt-1 max-w-3xl leading-relaxed">
          Does everything that came in go back out? Incoming is a QR payment; outgoing is
          the settlement paying it to the merchant. Every rupee of the difference should
          have a name.
        </p>
      </header>

      <div className="flex flex-wrap items-end gap-3 bg-white border border-neutral-200 rounded-lg px-4 py-3 shadow-sm">
        <label className="text-xs text-neutral-600">
          From
          <input type="date" value={from} max={localIso()} onChange={(e) => setFrom(e.target.value)}
            className="block mt-1 border border-neutral-300 rounded px-2 py-1 text-sm" />
        </label>
        <label className="text-xs text-neutral-600">
          To
          <input type="date" value={to} max={localIso()} onChange={(e) => setTo(e.target.value)}
            className="block mt-1 border border-neutral-300 rounded px-2 py-1 text-sm" />
        </label>
        <button type="button" onClick={() => void run()} disabled={!!job && !job.done}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white text-xs font-semibold disabled:opacity-50 cursor-pointer">
          {job && !job.done ? <FiRefreshCw className="animate-spin" /> : <FiPlay />}
          {job && !job.done ? "Reconciling…" : "Reconcile"}
        </button>

        {data && (
          <a href={reconcileApi.reportUrl(from, to)}
            className="ml-auto inline-flex items-center gap-1.5 px-3 py-2 rounded border border-neutral-300 hover:border-neutral-400 text-neutral-700 text-xs font-semibold cursor-pointer">
            <FiDownload /> Download report
          </a>
        )}
      </div>

      {/* The timeline. Knowing there are five stages, and which one is running,
          is most of what makes a minute-long wait bearable. */}
      {job && (
        <ol className="bg-white border border-neutral-200 rounded-lg shadow-sm divide-y divide-neutral-100">
          {job.steps.map((s, i) => {
            const state = job.done || i < job.step_index ? "done" : i === job.step_index ? "now" : "todo";
            return (
              <li key={s} className="flex items-center gap-3 px-4 py-2 text-xs">
                {state === "done" ? <FiCheckCircle className="text-emerald-600 shrink-0" />
                  : state === "now" ? <FiRefreshCw className="text-neutral-700 animate-spin shrink-0" />
                    : <FiClock className="text-neutral-300 shrink-0" />}
                <span className={state === "todo" ? "text-neutral-400" : "text-neutral-800"}>{s}</span>
                {state === "now" && (
                  <span className="ml-auto text-neutral-400 tabular-nums">{job.elapsed_seconds}s</span>
                )}
              </li>
            );
          })}
          {job.done && !job.error && (
            <li className="px-4 py-2 text-xs text-emerald-800 bg-emerald-50">
              Finished in {job.elapsed_seconds}s.
            </li>
          )}
        </ol>
      )}

      {error && (
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      )}

      {t && (
        <>
          <section className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div className="bg-white border border-neutral-200 rounded-lg shadow-sm p-5 text-sm">
              <h2 className="text-sm font-semibold text-neutral-900 mb-2">In and out</h2>
              <Row label={`Incoming — ${t.incoming_txns.toLocaleString()} QR payments`} value={money(t.incoming_amount)} />
              <Row label={`Settled — ${t.settled_txns.toLocaleString()} paid out`} value={money(t.settled_amount)} />
              <div className="border-t border-neutral-200 mt-2 pt-2">
                <Row label="Difference" value={money(t.difference)} strong />
              </div>
            </div>

            <div className={`border rounded-lg shadow-sm p-5 text-sm ${
              Math.abs(residual) < 0.01 && !t.amount_mismatches ? TONE.green : TONE.red
            }`}>
              <h2 className="text-sm font-semibold mb-2">Accounted for by</h2>
              <Row label="Settled later / reprocessed" value={money(t.settled_later_amount)} />
              <Row label="Settlement failed — outstanding" value={money(t.settlement_failed_amount)} />
              <Row label="Awaiting settlement" value={money(t.awaiting_amount)} />
              <Row label="Never raised for payment" value={money(t.not_in_report_amount)} />
              <Row label="Batch-settled merchants" value={money(t.batch_merchant_amount)} />
              <div className="border-t border-current/20 mt-2 pt-2">
                <Row label="Unexplained residual" value={money(residual)} strong />
                <Row label="Settled at a different amount than taken"
                  value={t.amount_mismatches.toLocaleString()} strong />
              </div>
              {/* Saying what the zero does and does not prove. Every payment is
                  in exactly one line above, so they always sum to the
                  difference -- calling that "balances exactly" flatters it. */}
              <p className="text-[11px] opacity-80 mt-3 leading-relaxed">{data?.balance_note}</p>
            </div>
          </section>

          <section className="grid grid-cols-1 md:grid-cols-4 gap-3">
            {BUCKETS.map((b) => {
              const rows = data?.buckets?.[b.key] ?? [];
              const active = openBucket === b.key;
              return (
                <button key={b.key} type="button"
                  onClick={() => setOpenBucket(active ? "" : b.key)}
                  title={b.blurb}
                  className={`text-left rounded-lg border px-4 py-3 shadow-sm cursor-pointer transition-colors ${
                    TONE[b.tone]} ${active ? "ring-2 ring-neutral-900/20" : ""}`}>
                  <p className="text-[11px] uppercase tracking-wide opacity-70">{b.label}</p>
                  <p className="text-xl font-semibold tabular-nums mt-0.5">{rows.length.toLocaleString()}</p>
                  <p className="text-[11px] opacity-70 mt-0.5">
                    {short(rows.reduce((s, r) => s + r.txn_amount, 0))}
                  </p>
                </button>
              );
            })}
          </section>

          {openBucket && (
            <section className="bg-white border border-neutral-200 rounded-lg shadow-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-neutral-100">
                <h2 className="text-sm font-semibold text-neutral-900">
                  {BUCKETS.find((b) => b.key === openBucket)?.label}
                </h2>
                <p className="text-[11px] text-neutral-500 mt-0.5">
                  {BUCKETS.find((b) => b.key === openBucket)?.blurb}
                </p>
              </div>
              <Table rows={data?.buckets?.[openBucket] ?? []} />
            </section>
          )}

          <section className="bg-white border border-neutral-200 rounded-lg shadow-sm p-5 text-sm space-y-3">
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">Batch-settled merchants</h2>
              <p className="text-[11px] text-neutral-500 mt-0.5 leading-relaxed">
                {t.batch_merchants} merchants are paid in batches rather than per payment, so
                they have no settlement row to match and reconcile on totals instead.
                {t.batch_unexplained > 0 && ` ${t.batch_unexplained} have a variance their balance does not explain.`}
              </p>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
              <div><p className="text-neutral-500">Payments taken</p><p className="font-semibold tabular-nums">{short(t.batch_took_amount)}</p></div>
              <div><p className="text-neutral-500">Paid out</p><p className="font-semibold tabular-nums">{short(t.batch_paid_amount)}</p></div>
              <div><p className="text-neutral-500">Merchants</p><p className="font-semibold tabular-nums">{t.batch_merchants}</p></div>
              <div><p className="text-neutral-500">Unexplained variance</p><p className="font-semibold tabular-nums">{t.batch_unexplained}</p></div>
            </div>
          </section>

          <section className="bg-white border border-neutral-200 rounded-lg shadow-sm p-5">
            <h2 className="text-sm font-semibold text-neutral-900 flex items-center gap-2">
              {t.orphan_realtime > 0 && <FiAlertTriangle className="text-amber-600" />}
              Settlements with no payment behind them
            </h2>
            <p className="text-xs text-neutral-600 mt-1 leading-relaxed">{data?.orphan_note}</p>
          </section>
        </>
      )}

      {!job && !data && (
        <p className="text-neutral-400 text-sm py-10">
          Pick a date range and press Reconcile. Matching one day against its settlements
          takes around eighty seconds.
        </p>
      )}
    </div>
  );
}
