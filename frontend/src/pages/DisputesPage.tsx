import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FiAlertTriangle, FiChevronDown, FiChevronRight, FiDatabase, FiLock,
  FiRefreshCw, FiSearch, FiDownload,
} from "react-icons/fi";
import { disputesApi, type CoreDbStatus, type Dispute, type DisputeResponse } from "../lib/api";

/**
 * Disputes: failed settlements whose money is still held on the merchant.
 *
 * Read live from the switch -- there is no upload here, which is the point.
 * A failure on its own is routine; a failure whose amount never left the
 * merchant's balance is a case someone has to chase, and only the pairing
 * shows that.
 */

const money = (n: number) =>
  `NPR ${n.toLocaleString("en-NP", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

const shortMoney = (n: number) => `NPR ${Math.round(n).toLocaleString("en-NP")}`;

function isoDaysAgo(days: number) {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

type Filter = "held" | "risk" | "all";

function Kpi({
  label, value, sub, tone = "neutral",
}: {
  label: string; value: string; sub?: string; tone?: "neutral" | "amber" | "red";
}) {
  const toneRing =
    tone === "red" ? "border-red-200 bg-red-50"
      : tone === "amber" ? "border-amber-200 bg-amber-50"
        : "border-neutral-200 bg-white";
  const toneText =
    tone === "red" ? "text-red-900" : tone === "amber" ? "text-amber-900" : "text-neutral-900";
  return (
    <div className={`rounded-lg border ${toneRing} px-4 py-3 shadow-sm`}>
      <p className="text-[11px] uppercase tracking-wide text-neutral-500">{label}</p>
      <p className={`text-xl font-semibold mt-0.5 tabular-nums ${toneText}`}>{value}</p>
      {sub && <p className="text-[11px] text-neutral-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function DetailGrid({ d }: { d: Dispute }) {
  // Everything the switch gave us for this row. Ops forwards these fields to
  // the aggregator verbatim, so they are shown raw rather than prettified.
  const groups: { title: string; rows: [string, string | number | null][] }[] = [
    {
      title: "Settlement",
      rows: [
        ["CRRN", d.crrn], ["STAN", d.stan], ["Ref ID", d.ref_id],
        ["Partner ref", d.partner_ref_id], ["Log ID", d.id],
        ["Amount", money(d.amount)], ["Service charge", money(d.service_charge)],
        ["Date / time", d.date_time || d.date],
        ["Frequency", d.settlement_frequency], ["Medium", d.medium],
      ],
    },
    {
      title: "Outcome",
      rows: [
        ["Status", d.status], ["Stopped at", d.current_status],
        ["Status code", d.status_code],
        ["Remarks", d.remarks], ["Remark 2", d.remark_two],
      ],
    },
    {
      title: "Merchant & balance",
      rows: [
        ["MID", d.mid], ["Merchant", d.merchant_name],
        ["Member code", d.member_code], ["Institution", d.institution_id],
        ["Total balance", money(d.total_balance)],
        ["Hold balance", money(d.hold_balance)],
      ],
    },
    {
      title: "Beneficiary & routing",
      rows: [
        ["Partner", d.partner], ["Acquirer", d.acquirer_name],
        ["Bank / wallet", d.bank_or_wallet], ["Wallet code", d.wallet_code],
        ["Creditor", d.creditor_name], ["Creditor a/c", d.creditor_account],
        ["Creditor mobile", d.creditor_mobile],
        ["Bank ID", d.bank_id], ["Branch ID", d.branch_id],
      ],
    },
  ];

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-5 px-4 pb-4 pt-1 bg-neutral-50 border-t border-neutral-200">
      {groups.map((g) => (
        <div key={g.title}>
          <p className="text-[11px] font-semibold uppercase tracking-wide text-neutral-500 mb-1.5">
            {g.title}
          </p>
          <dl className="space-y-1">
            {g.rows
              .filter(([, v]) => v !== null && v !== undefined && v !== "")
              .map(([k, v]) => (
                <div key={k} className="flex gap-2 text-xs">
                  <dt className="text-neutral-500 shrink-0 w-28">{k}</dt>
                  <dd className="text-neutral-800 font-medium break-all">{String(v)}</dd>
                </div>
              ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

export default function DisputesPage() {
  const [from, setFrom] = useState(isoDaysAgo(1));
  const [to, setTo] = useState(isoDaysAgo(0));
  const [data, setData] = useState<DisputeResponse | null>(null);
  const [status, setStatus] = useState<CoreDbStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("held");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => {
    disputesApi.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await disputesApi.list(from, to));
      setOpen(new Set());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load disputes");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [from, to]);

  useEffect(() => { void load(); }, [load]);

  const rows = useMemo(() => {
    const all = data?.disputes ?? [];
    const base =
      filter === "held" ? all.filter((d) => d.held || d.partially_held)
        : filter === "risk" ? all.filter((d) => d.double_pay_risk)
          : all;
    const q = search.trim().toLowerCase();
    if (!q) return base;
    return base.filter((d) =>
      [d.mid, d.crrn, d.merchant_name, d.reason, d.partner, d.creditor_account]
        .some((v) => (v || "").toString().toLowerCase().includes(q))
    );
  }, [data, filter, search]);

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const exportCsv = () => {
    const head = ["MID", "Merchant", "CRRN", "Amount", "Hold balance", "Reason",
      "Stopped at", "Partner", "Date", "Double-pay risk"];
    const body = rows.map((d) => [
      d.mid, d.merchant_name ?? "", d.crrn ?? "", d.amount, d.hold_balance,
      d.reason, d.current_status ?? "", d.partner ?? "", d.date,
      d.double_pay_risk ? "YES" : "",
    ]);
    const csv = [head, ...body]
      .map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(","))
      .join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `disputes_${from}_to_${to}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const t = data?.totals;

  return (
    <div className="max-w-7xl mx-auto px-8 py-10 space-y-6 font-sans">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">Disputes</h1>
          <p className="text-neutral-500 text-sm mt-1 max-w-3xl leading-relaxed">
            Failed settlements whose amount is still sitting in the merchant's hold balance.
            Read live from the switch — nothing to upload.
          </p>
        </div>

        {status && (
          <div
            className="flex items-center gap-2 text-xs rounded border border-neutral-200 bg-white px-3 py-2 shadow-sm"
            title={status.error ?? `${status.host}:${status.port}/${status.database}`}
          >
            <FiDatabase className={status.reachable ? "text-emerald-600" : "text-red-500"} />
            <span className="text-neutral-600">
              {status.reachable ? `Switch connected (${status.latency_ms} ms)` : "Switch unreachable"}
            </span>
            <span className="flex items-center gap-1 text-neutral-400 border-l border-neutral-200 pl-2 ml-1">
              <FiLock className="text-[11px]" /> read-only
            </span>
          </div>
        )}
      </header>

      <div className="flex flex-wrap items-end gap-3 bg-white border border-neutral-200 rounded-lg px-4 py-3 shadow-sm">
        <label className="text-xs text-neutral-600">
          From
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)}
            className="block mt-1 border border-neutral-300 rounded px-2 py-1 text-sm" />
        </label>
        <label className="text-xs text-neutral-600">
          To
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)}
            className="block mt-1 border border-neutral-300 rounded px-2 py-1 text-sm" />
        </label>
        <button type="button" onClick={() => void load()} disabled={loading}
          className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white text-xs font-semibold disabled:opacity-50 cursor-pointer">
          <FiRefreshCw className={loading ? "animate-spin" : ""} />
          {loading ? "Reading switch…" : "Refresh"}
        </button>

        <div className="relative ml-auto">
          <FiSearch className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-400 text-sm" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="MID, CRRN, merchant, reason…"
            className="pl-8 pr-3 py-2 border border-neutral-300 rounded text-sm w-72" />
        </div>

        <button type="button" onClick={exportCsv} disabled={!rows.length}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded border border-neutral-300 hover:border-neutral-400 text-neutral-700 text-xs font-semibold disabled:opacity-40 cursor-pointer">
          <FiDownload /> Export {rows.length ? `(${rows.length})` : ""}
        </button>
      </div>

      {error && (
        <div className="rounded border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </div>
      )}

      {t && (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          <Kpi label="Money held" value={shortMoney(t.held_amount)}
            sub={`${t.held_merchants} merchant${t.held_merchants === 1 ? "" : "s"}`} tone="amber" />
          <Kpi label="Disputes" value={t.held_count.toLocaleString()}
            sub="failed with balance held" tone="amber" />
          <Kpi label="Double-pay risk" value={t.at_risk_count.toLocaleString()}
            sub={shortMoney(t.at_risk_amount)} tone={t.at_risk_count ? "red" : "neutral"} />
          <Kpi label="All failures" value={t.failed.toLocaleString()}
            sub={`${t.merchants} merchants · ${shortMoney(t.failed_amount)}`} />
        </div>
      )}

      <div className="flex gap-1 border-b border-neutral-200">
        {([
          ["held", `Held${t ? ` (${t.held_count})` : ""}`],
          ["risk", `Double-pay risk${t ? ` (${t.at_risk_count})` : ""}`],
          ["all", `All failures${t ? ` (${t.failed})` : ""}`],
        ] as [Filter, string][]).map(([key, label]) => (
          <button key={key} type="button" onClick={() => setFilter(key)}
            className={`px-3.5 py-2 text-xs font-semibold border-b-2 -mb-px transition-colors cursor-pointer ${
              filter === key
                ? "border-neutral-900 text-neutral-900"
                : "border-transparent text-neutral-500 hover:text-neutral-800"
            }`}>
            {label}
          </button>
        ))}
      </div>

      {loading && <p className="text-neutral-400 text-sm py-10">Reading the switch…</p>}

      {!loading && !rows.length && (
        <p className="text-neutral-500 text-sm py-10">
          {filter === "held"
            ? "No failed settlement in this range has money still held on the merchant."
            : "Nothing matches."}
        </p>
      )}

      <div className="space-y-1.5">
        {rows.map((d, i) => {
          const key = `${d.id ?? d.crrn ?? d.mid}-${i}`;
          const isOpen = open.has(key);
          return (
            <div key={key}
              className={`bg-white border rounded-lg shadow-sm overflow-hidden ${
                d.double_pay_risk ? "border-amber-300" : "border-neutral-200"
              }`}>
              <button type="button" onClick={() => toggle(key)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-neutral-50 transition-colors cursor-pointer">
                {isOpen ? <FiChevronDown className="text-neutral-400 shrink-0" />
                  : <FiChevronRight className="text-neutral-400 shrink-0" />}

                <span className="font-mono text-sm text-neutral-900 shrink-0">{d.mid}</span>
                <span className="font-mono text-xs text-neutral-500 shrink-0 w-32">{d.crrn}</span>
                <span className="text-sm font-semibold tabular-nums text-neutral-900 shrink-0 w-32 text-right">
                  {money(d.amount)}
                </span>
                <span className="text-sm text-neutral-600 truncate flex-1">{d.reason}</span>

                {d.double_pay_risk && (
                  <span className="shrink-0 inline-flex items-center gap-1 text-[11px] font-semibold text-amber-800 bg-amber-100 border border-amber-200 rounded px-2 py-0.5">
                    <FiAlertTriangle className="text-[11px]" /> verify before retry
                  </span>
                )}
                {(d.held || d.partially_held) && (
                  <span className={`shrink-0 text-[11px] font-semibold rounded px-2 py-0.5 border ${
                    d.held
                      ? "text-emerald-800 bg-emerald-50 border-emerald-200"
                      : "text-neutral-700 bg-neutral-100 border-neutral-200"
                  }`}>
                    {d.held ? "held" : "part-held"} {shortMoney(d.hold_balance)}
                  </span>
                )}
              </button>
              {isOpen && <DetailGrid d={d} />}
            </div>
          );
        })}
      </div>
    </div>
  );
}
