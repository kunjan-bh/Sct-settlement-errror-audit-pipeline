import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  FiAlertTriangle, FiChevronDown, FiChevronRight, FiDatabase, FiLock,
  FiRefreshCw, FiSearch, FiDownload,
} from "react-icons/fi";
import SessionBar from "../components/SessionBar";
import {
  disputesApi, type CoreDbStatus, type Dispute, type DisputeOpStatus,
  type DisputeResponse, type DisputeScopeType,
} from "../lib/api";

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

type Filter = "held" | "risk" | "reprocessed" | "settled" | "all";

const OP_ACTIONS: { key: DisputeOpStatus; label: string; on: string }[] = [
  { key: "in_progress", label: "In Progress", on: "bg-blue-600 border-blue-600 text-white" },
  { key: "solved", label: "Solved", on: "bg-emerald-600 border-emerald-600 text-white" },
  { key: "exclude", label: "Exclude", on: "bg-neutral-700 border-neutral-700 text-white" },
];

/** What an entity-level exclusion should key on, most specific first. Which
 *  one applies depends on what the switch actually filled in for the row. */
function scopeChoices(d: Dispute): { type: DisputeScopeType; value: string; label: string }[] {
  const out: { type: DisputeScopeType; value: string; label: string }[] = [];
  if (d.mapped_partner) out.push({ type: "mapped_partner", value: d.mapped_partner, label: `${d.partner_type === "aggregator" ? "aggregator" : "wallet / bank"} “${d.mapped_partner}”` });
  if (d.bank_or_wallet) out.push({ type: "bank_or_wallet", value: d.bank_or_wallet, label: `institution “${d.bank_or_wallet}”` });
  if (d.mid) out.push({ type: "mid", value: d.mid, label: `merchant ${d.mid}` });
  return out;
}

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

function OpsBar({
  d, onSet, onScope, busy,
}: {
  d: Dispute;
  onSet: (s: DisputeOpStatus) => void;
  onScope: (type: DisputeScopeType, value: string) => void;
  busy: boolean;
}) {
  const [scopeOpen, setScopeOpen] = useState(false);
  const choices = scopeChoices(d);

  return (
    <div className="px-4 py-3 bg-white border-t border-neutral-200 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {OP_ACTIONS.map((a) => {
          const active = d.op_status === a.key;
          return (
            <button
              key={a.key}
              type="button"
              disabled={busy}
              // Clicking the active status again clears it back to pending, so
              // a mis-click is undone the same way it was made.
              onClick={() => onSet(active ? "pending" : a.key)}
              className={`px-3 py-1.5 rounded border text-xs font-semibold transition-colors disabled:opacity-50 cursor-pointer ${
                active ? a.on : "border-neutral-300 text-neutral-700 hover:border-neutral-400"
              }`}
            >
              {a.label}
            </button>
          );
        })}

        {d.op_scope && (
          <span className="text-[11px] text-neutral-500 ml-1">
            set by rule <span className="font-medium text-neutral-700">{d.op_scope}</span>
          </span>
        )}

        {!!choices.length && (
          <button
            type="button"
            onClick={() => setScopeOpen((v) => !v)}
            className="ml-auto text-[11px] text-neutral-500 hover:text-neutral-800 underline underline-offset-2 cursor-pointer"
          >
            Exclude a whole wallet / aggregator…
          </button>
        )}
      </div>

      {scopeOpen && (
        <div className="flex flex-wrap gap-2 pt-1">
          {choices.map((c) => (
            <button
              key={`${c.type}:${c.value}`}
              type="button"
              disabled={busy}
              onClick={() => { onScope(c.type, c.value); setScopeOpen(false); }}
              className="px-2.5 py-1 rounded border border-neutral-300 hover:border-neutral-500 text-[11px] text-neutral-700 disabled:opacity-50 cursor-pointer"
            >
              Exclude {c.label}
            </button>
          ))}
        </div>
      )}

      {d.negative_hold && (
        <p className="text-[11px] text-red-800 bg-red-50 border border-red-200 rounded px-2.5 py-1.5">
          This merchant's hold balance is {money(d.hold_balance)} — below zero, and exactly
          minus their total balance. That is a ledger problem, not a settled payment, so
          this failure is listed rather than ruled out. Worth raising with whoever owns
          the switch as well as chasing the settlement.
        </p>
      )}
      {d.likely_settled && (
        <p className="text-[11px] text-neutral-700 bg-neutral-100 border border-neutral-200 rounded px-2.5 py-1.5">
          This merchant holds {money(d.hold_balance)}, which their more recent failures
          already account for — so this older one most likely settled. Inferred from the
          balance, not stated by the switch; check before writing it off.
        </p>
      )}
      {d.settled_clear && !d.negative_hold && (
        <p className="text-[11px] text-neutral-700 bg-neutral-100 border border-neutral-200 rounded px-2.5 py-1.5">
          Hold and total balance are both zero — nothing is sitting on this merchant,
          so their settlements went through.
        </p>
      )}
      {d.reprocessed_ok && (
        <p className="text-[11px] text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-2.5 py-1.5">
          The same merchant and amount settled successfully at{" "}
          {d.reprocessed_at.slice(0, 19)} — this looks already reprocessed. Matched on
          merchant and amount, so check the CRRN before closing it.
        </p>
      )}
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
        ["Aggregator / wallet", d.mapped_partner], ["Mapped as", d.partner_type],
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
  const [entity, setEntity] = useState("");
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [busyKey, setBusyKey] = useState<string | null>(null);

  // Wallet/bank exclusions. Ticked means "not ours to chase" -- those rows
  // drop out of Disputes entirely, the way excluding an entity works in
  // Analytics. Unlike Analytics these persist, because deciding an aggregator
  // settles on its own schedule is an ops decision, not a view setting.
  const [excluded, setExcluded] = useState<string[]>([]);
  const [draftExcluded, setDraftExcluded] = useState<string[]>([]);
  const excludeDetailsRef = useRef<HTMLDetailsElement>(null);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    disputesApi.status().then(setStatus).catch(() => setStatus(null));
  }, []);

  const loadScopes = useCallback(async () => {
    try {
      const s = await disputesApi.scopes();
      setExcluded(s.filter((x) => x.scope_type === "mapped_partner").map((x) => x.scope_value));
    } catch {
      setExcluded([]);
    }
  }, []);

  useEffect(() => { void loadScopes(); }, [loadScopes]);

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

  // Record a decision, then patch just that row in place. Re-fetching would
  // re-read the switch (~19s for a two-day range) and collapse the row the
  // operator is working in, which makes triaging a list unusable.
  const saveStatus = async (d: Dispute, status: DisputeOpStatus) => {
    const key = String(d.id ?? "");
    setBusyKey(key);
    setError(null);
    try {
      await disputesApi.setStatus(key, {
        status, mid: d.mid, crrn: d.crrn, amount: d.amount,
      });
      setData((prev) => prev && {
        ...prev,
        disputes: prev.disputes.map((x) =>
          x.id === d.id ? { ...x, op_status: status, op_scope: null } : x
        ),
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save that decision");
    } finally {
      setBusyKey(null);
    }
  };

  // An entity-level exclusion changes many rows at once, so this one does
  // reload -- there is no way to know locally which rows it swept.
  const setScope = async (type: DisputeScopeType, value: string) => {
    setBusyKey(value);
    setError(null);
    try {
      await disputesApi.setScope(type, value, { status: "exclude" });
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not apply that exclusion");
    } finally {
      setBusyKey(null);
    }
  };

  // Every wallet/bank present in the range, commonest first, so the list is
  // ordered by how much noise each one is actually making.
  const availableEntities = useMemo(() => {
    const counts = new Map<string, number>();
    for (const d of data?.disputes ?? []) {
      const key = d.mapped_partner;
      if (key) counts.set(key, (counts.get(key) ?? 0) + 1);
    }
    return [...counts.entries()]
      .map(([entity, count]) => ({ entity, count }))
      .sort((a, b) => b.count - a.count);
  }, [data]);

  const applyExcluded = async () => {
    setApplying(true);
    setError(null);
    try {
      const added = draftExcluded.filter((e) => !excluded.includes(e));
      const removed = excluded.filter((e) => !draftExcluded.includes(e));
      // "pending" clears a rule rather than storing one, so unticking really
      // brings the entity back rather than pinning it to a status.
      await Promise.all([
        ...added.map((e) => disputesApi.setScope("mapped_partner", e, { status: "exclude" })),
        ...removed.map((e) => disputesApi.setScope("mapped_partner", e, { status: "pending" })),
      ]);
      if (excludeDetailsRef.current) excludeDetailsRef.current.open = false;
      await loadScopes();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not apply exclusions");
    } finally {
      setApplying(false);
    }
  };

  const rows = useMemo(() => {
    const all = data?.disputes ?? [];
    const live = all.filter(
      (d) => d.op_status !== "exclude" && !d.reprocessed_ok && !d.likely_settled
    );
    const base =
      filter === "held" ? live.filter((d) => d.held || d.partially_held || d.negative_hold)
        : filter === "risk" ? live.filter((d) => d.double_pay_risk)
          : filter === "reprocessed" ? all.filter((d) => d.reprocessed_ok && d.op_status !== "exclude")
              // "All failures" means all except excluded: an excluded entity
              // is gone from this tab, not merely filed under another one.
              : all.filter((d) => d.op_status !== "exclude");
    const scoped = entity ? base.filter((d) => d.mapped_partner === entity) : base;
    const q = search.trim().toLowerCase();
    if (!q) return scoped;
    return scoped.filter((d) =>
      [d.mid, d.crrn, d.merchant_name, d.reason, d.mapped_partner, d.bank_or_wallet, d.creditor_account]
        .some((v) => (v || "").toString().toLowerCase().includes(q))
    );
  }, [data, filter, search, entity]);

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const exportCsv = () => {
    const head = ["MID", "Merchant", "CRRN", "Amount", "Hold balance", "Reason",
      "Stopped at", "Aggregator / wallet", "Institution", "Date", "Double-pay risk"];
    const body = rows.map((d) => [
      d.mid, d.merchant_name ?? "", d.crrn ?? "", d.amount, d.hold_balance,
      d.reason, d.current_status ?? "", d.mapped_partner, d.bank_or_wallet ?? "", d.date,
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

      {/* The session is the batch. It sits directly above the list it records
          so starting one, working, and closing one are the same motion. */}
      <SessionBar onChanged={() => void load()} />

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

        <select
          value={entity}
          onChange={(e) => setEntity(e.target.value)}
          className="ml-auto border border-neutral-300 rounded px-2 py-2 text-sm text-neutral-700 max-w-56"
        >
          <option value="">All aggregators / wallets</option>
          {availableEntities.map((e) => (
            <option key={e.entity} value={e.entity}>{e.entity} ({e.count})</option>
          ))}
        </select>

        <div className="relative">
          <FiSearch className="absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-400 text-sm" />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="MID, CRRN, merchant, reason…"
            className="pl-8 pr-3 py-2 border border-neutral-300 rounded text-sm w-72" />
        </div>

        <details
          ref={excludeDetailsRef}
          className="relative"
          onToggle={(e) => {
            if ((e.currentTarget as HTMLDetailsElement).open) setDraftExcluded(excluded);
          }}
        >
          <summary
            className={`list-none cursor-pointer select-none px-3 py-2 rounded text-xs font-semibold border transition-colors inline-flex items-center gap-1.5 ${
              excluded.length > 0
                ? "bg-red-50 border-red-200 text-red-700"
                : "border-neutral-300 text-neutral-700 hover:border-neutral-400"
            }`}
          >
            Exclude{excluded.length > 0 ? ` (${excluded.length})` : ""}
            <FiChevronDown className="text-[10px]" />
          </summary>
          <div className="absolute right-0 z-20 mt-2 w-80 max-h-96 flex flex-col bg-white border border-neutral-200 rounded-lg shadow-lg p-2">
            <p className="text-[11px] text-neutral-400 px-2 pt-1 pb-2 leading-snug">
              Tick an aggregator or wallet to drop it from Disputes — for ones that
              settle on their own schedule and are not ours to chase. Names come from
              Partner Mapping, so they match the rest of the app. Nothing changes
              until you click OK.
            </p>
            <div className="overflow-y-auto">
              {availableEntities.length === 0 && (
                <p className="text-xs text-neutral-400 px-2 py-3">No entities in range yet.</p>
              )}
              {availableEntities.map((e) => (
                <label
                  key={e.entity}
                  className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-neutral-50 text-sm cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={draftExcluded.includes(e.entity)}
                    onChange={() =>
                      setDraftExcluded((prev) =>
                        prev.includes(e.entity)
                          ? prev.filter((x) => x !== e.entity)
                          : [...prev, e.entity]
                      )
                    }
                    className="rounded border-neutral-300"
                  />
                  <span className="flex-1 text-neutral-700 truncate">{e.entity}</span>
                  <span className="text-[11px] text-neutral-400 tabular-nums">{e.count}</span>
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2 pt-2 mt-1 border-t border-neutral-100">
              <button type="button" disabled={applying} onClick={() => void applyExcluded()}
                className="px-3 py-1.5 rounded bg-neutral-900 hover:bg-neutral-800 text-white text-xs font-semibold disabled:opacity-50 cursor-pointer">
                {applying ? "Applying…" : "OK"}
              </button>
            </div>
          </div>
        </details>

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
            sub={`${t.held_merchants} merchant${t.held_merchants === 1 ? "" : "s"} — what the holds cover`} tone="amber" />
          <Kpi label="Disputes" value={t.held_count.toLocaleString()}
            sub={`${t.open_count} open · ${t.solved_count} solved`} tone="amber" />
          <Kpi label="Double-pay risk" value={t.at_risk_count.toLocaleString()}
            sub={shortMoney(t.at_risk_amount)} tone={t.at_risk_count ? "red" : "neutral"} />
          {!!t.negative_hold_count && (
            <Kpi label="Negative hold" value={t.negative_hold_count.toLocaleString()}
              sub="ledger problem — hold below zero" tone="red" />
          )}
          <Kpi label="All failures" value={t.failed.toLocaleString()}
            sub={`${t.merchants} merchants · ${t.reprocessed_count} reprocessed`} />
        </div>
      )}

      <div className="flex gap-1 border-b border-neutral-200">
        {([
          ["held", `Disputes${t ? ` (${t.held_count})` : ""}`],
          ["risk", `Double-pay risk${t ? ` (${t.at_risk_count})` : ""}`],
          ["reprocessed", `Already reprocessed${t ? ` (${t.reprocessed_count})` : ""}`],
          ["settled", `Covered by balance${t ? ` (${t.likely_settled_count})` : ""}`],
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
            : filter === "reprocessed"
              ? "Nothing in this range settled successfully on a later attempt."
              : filter === "settled"
                ? "No failure was ruled out by the merchant's balance."
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
                <span className="text-xs text-neutral-500 shrink-0 w-32 truncate" title={d.bank_or_wallet ?? ""}>
                  {d.mapped_partner}
                </span>
                <span className="text-sm text-neutral-600 truncate flex-1">{d.reason}</span>

                {d.op_status !== "pending" && (
                  <span className={`shrink-0 text-[11px] font-semibold rounded px-2 py-0.5 border ${
                    d.op_status === "solved" ? "text-emerald-800 bg-emerald-50 border-emerald-200"
                      : d.op_status === "in_progress" ? "text-blue-800 bg-blue-50 border-blue-200"
                        : "text-neutral-600 bg-neutral-100 border-neutral-200"
                  }`}>
                    {d.op_status === "in_progress" ? "in progress" : d.op_status}
                  </span>
                )}
                {d.double_pay_risk && (
                  <span className="shrink-0 inline-flex items-center gap-1 text-[11px] font-semibold text-amber-800 bg-amber-100 border border-amber-200 rounded px-2 py-0.5">
                    <FiAlertTriangle className="text-[11px]" /> verify before retry
                  </span>
                )}
                {d.negative_hold && (
                  <span className="shrink-0 inline-flex items-center gap-1 text-[11px] font-semibold text-red-800 bg-red-50 border border-red-200 rounded px-2 py-0.5">
                    <FiAlertTriangle className="text-[11px]" /> negative hold {shortMoney(d.hold_balance)}
                  </span>
                )}
                {(d.held || d.partially_held) && (
                  <span className={`shrink-0 text-[11px] font-semibold rounded px-2 py-0.5 border ${
                    d.held
                      ? "text-emerald-800 bg-emerald-50 border-emerald-200"
                      : "text-neutral-700 bg-neutral-100 border-neutral-200"
                  }`}>
                    {d.held ? `held ${shortMoney(d.amount)}` : `part-held ${shortMoney(d.covered_amount ?? 0)}`}
                  </span>
                )}
              </button>
              {isOpen && (
                <>
                  <OpsBar
                    d={d}
                    busy={busyKey === String(d.id ?? "")}
                    onSet={(s) => void saveStatus(d, s)}
                    onScope={(type, value) => void setScope(type, value)}
                  />
                  <DetailGrid d={d} />
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
