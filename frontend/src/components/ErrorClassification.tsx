import { useEffect, useMemo, useState } from "react";
import { FiDownload, FiChevronDown, FiChevronRight, FiRefreshCw } from "react-icons/fi";
import {
  batchesApi,
  type ErrorClassificationData,
  type ErrorEntity,
  type EntityType,
  type ErrorSide,
  type RetryResolvedRow,
} from "../lib/api";
import { cacheGet, cacheSet } from "../lib/cache";

/**
 * Error Classification view.
 *
 * Answers "what broke, on whose side, how often" -- deliberately NOT filtered
 * by ops status, unlike the Solve tab. An issue someone excluded still
 * happened, and hiding it would understate an aggregator's real error rate.
 *
 * Color: the six categorical slots are assigned to the six most frequent
 * categories BATCH-WIDE, once, and reused everywhere in this view -- so a
 * category is the same color in the frequency chart and in every entity's
 * composition bar. The tail folds into one gray "Other" rather than
 * generating a 7th hue (which nobody could tell apart under CVD anyway).
 */

// Validated categorical slots (adjacent CVD ΔE 9.1, normal-vision 19.6 on a
// white surface). Three of them sit below 3:1 contrast, which is why every
// color here is always accompanied by a visible label and the full table.
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];
const OTHER_COLOR = "#898781";
const BAR_HUE = "#2a78d6"; // single-series magnitude bars

const ENTITY_TYPE_LABEL: Record<EntityType, string> = {
  aggregator: "Aggregator",
  bank_wallet: "Bank / Wallet",
  sct: "SCT",
  unmapped: "Unmapped MID",
};

const ENTITY_TYPE_STYLE: Record<EntityType, string> = {
  aggregator: "bg-blue-50 text-blue-700 border-blue-200",
  bank_wallet: "bg-violet-50 text-violet-700 border-violet-200",
  sct: "bg-neutral-900 text-white border-neutral-900",
  unmapped: "bg-neutral-100 text-neutral-500 border-neutral-300",
};

const SIDE_LABEL: Record<ErrorSide, string> = {
  sct: "SCT",
  aggregator: "Aggregator",
  bank: "Bank",
  unknown: "Unknown",
};

const nf = new Intl.NumberFormat();

const cacheKeyFor = (batchId: number) => `error-classification:${batchId}`;

export default function ErrorClassification({ batchId }: { batchId: number }) {
  const cached = cacheGet<ErrorClassificationData>(cacheKeyFor(batchId));
  const [data, setData] = useState<ErrorClassificationData | null>(cached ?? null);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Not filtered by ops status (see file header), so nothing outside a
    // fresh upload changes this data -- once a batch's classification is
    // loaded, reuse it across tab switches instead of re-hitting the
    // backend's full re-aggregation every time.
    const alreadyCached = cacheGet<ErrorClassificationData>(cacheKeyFor(batchId));
    if (alreadyCached) {
      setData(alreadyCached);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    batchesApi
      .errorClassification(batchId)
      .then((d) => {
        if (cancelled) return;
        cacheSet(cacheKeyFor(batchId), d);
        setData(d);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  // category name -> color, fixed batch-wide by overall frequency
  const colorOf = useMemo(() => {
    const map = new Map<string, string>();
    if (!data) return map;
    const seen: string[] = [];
    for (const row of data.categories) {
      if (!seen.includes(row.category)) seen.push(row.category);
    }
    seen.forEach((name, i) => map.set(name, i < SERIES.length ? SERIES[i] : OTHER_COLOR));
    return map;
  }, [data]);

  if (loading) return <p className="text-neutral-400 text-sm py-10">Loading error classification…</p>;
  if (error) return <p className="text-red-600 text-sm py-10">{error}</p>;
  if (!data) return null;

  const { totals, entities, categories } = data;

  if (totals.total_errors === 0) {
    return (
      <div className="bg-white border border-neutral-200 rounded-lg p-10 text-center shadow-sm">
        <p className="text-neutral-800 font-medium">No failed settlements in this batch</p>
        <p className="text-neutral-400 text-sm mt-1">Every transaction settled successfully.</p>
      </div>
    );
  }

  const topEntities = entities.slice(0, 12);
  const maxEntityTotal = topEntities[0]?.total ?? 1;
  const topCategories = categories.slice(0, 10);
  const maxCategoryCount = topCategories[0]?.count ?? 1;
  const namedCategories = [...colorOf.entries()].filter(([, c]) => c !== OTHER_COLOR);
  const hasOther = colorOf.size > SERIES.length;

  return (
    <div className="space-y-8">
      {/* Header + raw download */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-neutral-900">Error Classification</h2>
          <p className="text-sm text-neutral-500 mt-0.5 max-w-2xl leading-relaxed">
            Every non-settled transaction, grouped by who it belongs to and what went wrong.
            Counts are independent of ops status — excluded and solved issues still count here,
            because they still happened.
          </p>
        </div>
        <a
          href={batchesApi.getErrorClassificationUrl(batchId)}
          download
          className="shrink-0 inline-flex items-center gap-1.5 px-3.5 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs transition-colors shadow-sm cursor-pointer"
          title="Download the classification table as Excel (numbers only, no charts)"
        >
          <FiDownload className="text-sm" />
          Download Raw Excel
        </a>
      </div>

      {/* KPI row */}
      <section className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="bg-white border border-neutral-200 rounded-lg px-4 py-3 shadow-sm">
          <p className="text-xs text-neutral-500">Total error transactions</p>
          <p className="text-3xl font-semibold text-neutral-900 mt-1">{nf.format(totals.total_errors)}</p>
          <p className="text-[11px] text-neutral-400 mt-1 tabular-nums">
            {nf.format(totals.by_status.failed)} failed · {nf.format(totals.by_status.pending)} pending ·{" "}
            {nf.format(totals.by_status.lo_progress)} lo progress
          </p>
          {totals.retry_resolved > 0 && (
            <p className="text-[11px] text-emerald-700 mt-1 tabular-nums">
              +{nf.format(totals.retry_resolved)} excluded as reprocessed
            </p>
          )}
        </div>
        <div className="bg-white border border-neutral-200 rounded-lg px-4 py-3 shadow-sm">
          <p className="text-xs text-neutral-500">Entities affected</p>
          <p className="text-3xl font-semibold text-neutral-900 mt-1">{totals.entities}</p>
          <p className="text-[11px] text-neutral-400 mt-1">aggregators, wallets and SCT</p>
        </div>
        <div className="bg-white border border-neutral-200 rounded-lg px-4 py-3 shadow-sm">
          <p className="text-xs text-neutral-500">Distinct error categories</p>
          <p className="text-3xl font-semibold text-neutral-900 mt-1">{totals.categories}</p>
          <p className="text-[11px] text-neutral-400 mt-1">
            top one is {topCategories[0]?.share_of_batch.toFixed(1)}% of the batch
          </p>
        </div>
        <div className="bg-white border border-neutral-200 rounded-lg px-4 py-3 shadow-sm">
          <p className="text-xs text-neutral-500">By fault side</p>
          <div className="mt-2 space-y-1">
            {Object.entries(totals.by_side).map(([side, count]) => (
              <div key={side} className="flex items-center justify-between text-xs">
                <span className="text-neutral-600">{SIDE_LABEL[side as ErrorSide] ?? side}</span>
                <span className="font-semibold text-neutral-900 tabular-nums">{nf.format(count)}</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Chart 1 — magnitude ranking, one series so no legend needed */}
      <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
        <h3 className="text-sm font-semibold text-neutral-900">Failed settlements by entity</h3>
        <p className="text-xs text-neutral-500 mt-0.5 mb-4">
          {entities.length > topEntities.length
            ? `Top ${topEntities.length} of ${entities.length} entities, worst first`
            : "Worst first"}
        </p>
        <div className="space-y-2.5">
          {topEntities.map((e) => (
            <div key={e.entity} className="flex items-center gap-3">
              <div className="w-40 sm:w-52 shrink-0 text-xs text-neutral-700 truncate" title={e.entity}>
                {e.entity}
              </div>
              <div className="flex-1 flex items-center gap-2 min-w-0">
                <div
                  className="h-4"
                  style={{
                    width: `${Math.max((e.total / maxEntityTotal) * 100, 0.8)}%`,
                    background: BAR_HUE,
                    borderRadius: "0 4px 4px 0",
                  }}
                  title={`${e.entity}: ${nf.format(e.total)} error txns (${e.share_of_batch.toFixed(1)}% of batch)`}
                />
                <span className="text-xs font-semibold text-neutral-800 tabular-nums shrink-0">
                  {nf.format(e.total)}
                </span>
                <span className="text-[11px] text-neutral-400 tabular-nums shrink-0">
                  {e.share_of_batch.toFixed(1)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Category legend — identity channel for chart 2 and the composition bars */}
      <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm space-y-5">
        <div>
          <h3 className="text-sm font-semibold text-neutral-900">Most frequent error categories</h3>
          <p className="text-xs text-neutral-500 mt-0.5">
            Across the whole batch. These colors identify the same category everywhere below.
          </p>
        </div>

        <div className="flex flex-wrap gap-x-4 gap-y-2">
          {namedCategories.map(([name, color]) => (
            <span key={name} className="inline-flex items-center gap-1.5 text-xs text-neutral-600">
              <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: color }} />
              {name}
            </span>
          ))}
          {hasOther && (
            <span className="inline-flex items-center gap-1.5 text-xs text-neutral-600">
              <span className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: OTHER_COLOR }} />
              Other categories
            </span>
          )}
        </div>

        <div className="space-y-2.5">
          {topCategories.map((c) => (
            <div key={`${c.category}-${c.side}`} className="flex items-center gap-3">
              <div className="w-40 sm:w-64 shrink-0 text-xs text-neutral-700 truncate" title={c.category}>
                {c.category}
              </div>
              <span className="shrink-0 text-[10px] px-1.5 py-0.5 rounded border border-neutral-200 text-neutral-500">
                {SIDE_LABEL[c.side] ?? c.side}
              </span>
              <div className="flex-1 flex items-center gap-2 min-w-0">
                <div
                  className="h-4"
                  style={{
                    width: `${Math.max((c.count / maxCategoryCount) * 100, 0.8)}%`,
                    background: colorOf.get(c.category) ?? OTHER_COLOR,
                    borderRadius: "0 4px 4px 0",
                  }}
                  title={`${c.category}: ${nf.format(c.count)} txns across ${c.entity_count} entities`}
                />
                <span className="text-xs font-semibold text-neutral-800 tabular-nums shrink-0">
                  {nf.format(c.count)}
                </span>
                <span className="text-[11px] text-neutral-400 tabular-nums shrink-0">
                  {c.share_of_batch.toFixed(1)}%
                </span>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Suppressed-but-auditable: failures a reprocess already settled */}
      {totals.retry_resolved > 0 && <RetryResolvedPanel rows={data.retry_resolved_rows} />}

      {/* Per-entity breakdown */}
      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-neutral-900">Breakdown per entity</h3>
        {entities.map((entity) => (
          <EntityCard key={entity.entity} entity={entity} colorOf={colorOf} />
        ))}
      </section>
    </div>
  );
}

/**
 * The counts above deliberately hide these rows, so the hiding has to be
 * inspectable: every suppressed failure is listed with the exact success
 * that settled it (time, mode and STAN), and whether that success arrived in
 * this upload or a later one.
 */
function RetryResolvedPanel({ rows }: { rows: RetryResolvedRow[] }) {
  const [open, setOpen] = useState(false);
  const crossBatch = rows.filter((r) => !r.same_batch).length;

  const when = (iso: string | null) =>
    iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";

  return (
    <section className="bg-white border border-neutral-200 rounded-lg shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-5 py-4 flex items-start gap-3 cursor-pointer"
      >
        <FiRefreshCw className="text-emerald-600 mt-0.5 shrink-0" />
        <div className="flex-1">
          <h3 className="text-sm font-semibold text-neutral-900">
            {nf.format(rows.length)} failed settlement{rows.length === 1 ? "" : "s"} excluded — already
            reprocessed and settled
          </h3>
          <p className="text-xs text-neutral-500 mt-0.5 leading-relaxed">
            Matched to a later <strong>On Call</strong> or <strong>System Default</strong> success with the
            same MID, amount and beneficiary account.
            {crossBatch > 0 && ` ${nf.format(crossBatch)} were settled in a different upload than the one that recorded the failure.`}{" "}
            They are not counted as errors anywhere above.
          </p>
        </div>
        {open ? (
          <FiChevronDown className="text-neutral-400 mt-0.5 shrink-0" />
        ) : (
          <FiChevronRight className="text-neutral-400 mt-0.5 shrink-0" />
        )}
      </button>

      {open && (
        <div className="border-t border-neutral-100 overflow-auto max-h-[28rem]">
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 z-10">
              <tr className="bg-neutral-50 text-[11px] font-semibold text-neutral-500 uppercase tracking-wider">
                <th className="py-2 px-5">MID</th>
                <th className="py-2 px-3">Partner</th>
                <th className="py-2 px-3 text-right">Amount</th>
                <th className="py-2 px-3">Beneficiary</th>
                <th className="py-2 px-3">Failed at</th>
                <th className="py-2 px-3">Original failure</th>
                <th className="py-2 px-3">Settled at</th>
                <th className="py-2 px-5">Settled by</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {rows.map((r, i) => (
                <tr key={`${r.failed_stan}-${i}`} className="text-xs text-neutral-700">
                  <td className="py-2 px-5 font-mono">{r.mid}</td>
                  <td className="py-2 px-3">{r.partner_name}</td>
                  <td className="py-2 px-3 text-right tabular-nums">
                    {r.amount === null ? "—" : nf.format(r.amount)}
                  </td>
                  <td className="py-2 px-3 font-mono text-neutral-500">{r.beneficiary_id}</td>
                  <td className="py-2 px-3 tabular-nums whitespace-nowrap">
                    {when(r.failed_at)}
                    <span className="text-neutral-400"> · {r.failed_stan}</span>
                  </td>
                  <td className="py-2 px-3 text-neutral-500 max-w-[16rem] truncate" title={r.failed_remark ?? ""}>
                    {r.failed_remark}
                  </td>
                  <td className="py-2 px-3 tabular-nums whitespace-nowrap text-emerald-700">
                    {when(r.settled_at)}
                    <span className="text-emerald-600/70"> · {r.settled_stan}</span>
                  </td>
                  <td className="py-2 px-5 whitespace-nowrap">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="text-[10px] px-1.5 py-0.5 rounded border border-emerald-200 bg-emerald-50 text-emerald-700">
                        {r.settled_by}
                      </span>
                      {!r.same_batch && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded border border-neutral-200 text-neutral-500">
                          other upload
                        </span>
                      )}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function EntityCard({
  entity,
  colorOf,
}: {
  entity: ErrorEntity;
  colorOf: Map<string, string>;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="bg-white border border-neutral-200 rounded-lg shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full text-left px-5 py-4 cursor-pointer"
      >
        <div className="flex items-center gap-3 flex-wrap">
          {open ? (
            <FiChevronDown className="text-neutral-400 shrink-0" />
          ) : (
            <FiChevronRight className="text-neutral-400 shrink-0" />
          )}
          <span className="font-semibold text-neutral-900 text-sm">{entity.entity}</span>
          <span
            className={`text-[10px] font-medium px-1.5 py-0.5 rounded border ${ENTITY_TYPE_STYLE[entity.entity_type]}`}
          >
            {ENTITY_TYPE_LABEL[entity.entity_type]}
          </span>
          <span className="ml-auto flex items-center gap-2 text-[11px] tabular-nums">
            <span className="text-neutral-500">
              {entity.categories.length} categor{entity.categories.length === 1 ? "y" : "ies"}
            </span>
            <span className="font-semibold text-neutral-900">{nf.format(entity.total)} txns</span>
            <span className="text-neutral-400">{entity.share_of_batch.toFixed(1)}%</span>
          </span>
        </div>

        {/* Composition: part-to-whole across this entity's categories.
            2px surface gaps do the separating, no strokes. */}
        <div className="flex gap-[2px] mt-3 ml-7">
          {entity.categories.map((c) => (
            <div
              key={`${c.category}-${c.side}`}
              className="h-3 rounded-sm"
              style={{
                width: `${c.share_of_entity}%`,
                background: colorOf.get(c.category) ?? OTHER_COLOR,
              }}
              title={`${c.category}: ${nf.format(c.count)} txns (${c.share_of_entity.toFixed(1)}% of ${entity.entity})`}
            />
          ))}
        </div>

        <div className="flex gap-3 mt-2 ml-7 text-[11px] text-neutral-500 tabular-nums">
          <span>{nf.format(entity.failed)} failed</span>
          <span>·</span>
          <span>{nf.format(entity.pending)} pending</span>
          <span>·</span>
          <span>{nf.format(entity.lo_progress)} lo progress</span>
        </div>
      </button>

      {open && (
        <div className="border-t border-neutral-100 overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-neutral-50 text-[11px] font-semibold text-neutral-500 uppercase tracking-wider">
                <th className="py-2 px-5">Issue Category</th>
                <th className="py-2 px-3">Side</th>
                <th className="py-2 px-3 text-right">Failed</th>
                <th className="py-2 px-3 text-right">Pending</th>
                <th className="py-2 px-3 text-right">Lo Progress</th>
                <th className="py-2 px-3 text-right">Total</th>
                <th className="py-2 px-5 text-right">Share</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-neutral-100">
              {entity.categories.map((c) => (
                <tr key={`${c.category}-${c.side}`} className="text-xs text-neutral-700">
                  <td className="py-2 px-5">
                    <span className="inline-flex items-center gap-2">
                      <span
                        className="w-2.5 h-2.5 rounded-sm shrink-0"
                        style={{ background: colorOf.get(c.category) ?? OTHER_COLOR }}
                      />
                      {c.category}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-neutral-500">{SIDE_LABEL[c.side] ?? c.side}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{nf.format(c.failed)}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{nf.format(c.pending)}</td>
                  <td className="py-2 px-3 text-right tabular-nums">{nf.format(c.lo_progress)}</td>
                  <td className="py-2 px-3 text-right tabular-nums font-semibold text-neutral-900">
                    {nf.format(c.count)}
                  </td>
                  <td className="py-2 px-5 text-right tabular-nums text-neutral-500">
                    {c.share_of_entity.toFixed(1)}%
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
