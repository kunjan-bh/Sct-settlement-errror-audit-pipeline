import { useEffect, useMemo, useRef, useState } from "react";
import { FiChevronDown } from "react-icons/fi";
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
import {
  analyticsApi,
  type AnalyticsData,
  type AnalyticsBucket,
  type AnalyticsAvailableEntity,
  type EntityType,
} from "../lib/api";
import { cacheGet, cacheSet } from "../lib/cache";
import StatCard from "../components/StatCard";

/**
 * Cross-batch Analytics view. Unlike every other page in this app, this one
 * is not scoped to a single batch -- it spans whatever batches fall inside
 * the selected date range (see analyticsApi.get / analytics_service.py).
 *
 * Colors reuse the same semantics as the rest of the app: red=Failed,
 * amber=Pending, blue=Lo Progress (matches the "Lo Progress" badge color on
 * DashboardPage), green=Solved (matches the report's solved-status fill).
 */

const STATUS_COLORS = { failed: "#dc2626", pending: "#d97706", lo_progress: "#2563eb" };
const RESOLUTION_COLORS = { solved: "#16a34a", unsolved: "#a8a29e" };
const ENTITY_COLOR = "#2a78d6";
const CATEGORY_COLOR = "#eb6834";

const nf = new Intl.NumberFormat();

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

const BUCKETS: { key: AnalyticsBucket; label: string }[] = [
  { key: "day", label: "Day" },
  { key: "week", label: "Week" },
  { key: "month", label: "Month" },
];

const ENTITY_TYPE_LABEL: Record<EntityType, string> = {
  aggregator: "Aggregator",
  bank_wallet: "Bank / Wallet",
  sct: "SCT",
  unmapped: "Unmapped",
};

const cacheKeyFor = (from: string, to: string, bucket: AnalyticsBucket, excluded: string[]) =>
  `analytics:${from}:${to}:${bucket}:${[...excluded].sort().join("|")}`;

export default function AnalyticsPage() {
  const initial = useMemo(() => presetRange("30d"), []);
  const [from, setFrom] = useState(initial.from);
  const [to, setTo] = useState(initial.to);
  const [bucket, setBucket] = useState<AnalyticsBucket>("day");
  // Page-level filter: entities excluded here are dropped from every number
  // (KPIs/trend/donut/top lists), unlike the per-issue "exclude" ops status
  // in the Solve tab, which Analytics deliberately still counts.
  //
  // Checking boxes only edits `draftExcluded` -- nothing refetches until OK
  // is clicked, which commits it to `excludedEntities` (the actual fetch
  // dependency). Reopening the picker re-syncs the draft from the last
  // applied value, so an abandoned selection never lingers.
  const [excludedEntities, setExcludedEntities] = useState<string[]>([]);
  const [draftExcluded, setDraftExcluded] = useState<string[]>([]);
  const excludeDetailsRef = useRef<HTMLDetailsElement>(null);

  const cached = cacheGet<AnalyticsData>(cacheKeyFor(from, to, bucket, excludedEntities));
  const [data, setData] = useState<AnalyticsData | null>(cached ?? null);
  const [loading, setLoading] = useState(!cached);
  const [error, setError] = useState<string | null>(null);
  // Kept separate from `data` so the exclude picker's checklist doesn't
  // disappear while a re-filtered fetch is in flight.
  const [availableEntities, setAvailableEntities] = useState<AnalyticsAvailableEntity[]>(
    cached?.available_entities ?? []
  );

  useEffect(() => {
    const alreadyCached = cacheGet<AnalyticsData>(cacheKeyFor(from, to, bucket, excludedEntities));
    if (alreadyCached) {
      setData(alreadyCached);
      setAvailableEntities(alreadyCached.available_entities);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    analyticsApi
      .get(from, to, bucket, excludedEntities)
      .then((d) => {
        if (cancelled) return;
        cacheSet(cacheKeyFor(from, to, bucket, excludedEntities), d);
        setData(d);
        setAvailableEntities(d.available_entities);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [from, to, bucket, excludedEntities]);

  const toggleDraftExcluded = (entity: string) => {
    setDraftExcluded((prev) =>
      prev.includes(entity) ? prev.filter((e) => e !== entity) : [...prev, entity]
    );
  };

  const applyExcluded = () => {
    setExcludedEntities(draftExcluded);
    if (excludeDetailsRef.current) excludeDetailsRef.current.open = false;
  };

  const donutData = useMemo(() => {
    if (!data) return { outer: [], inner: [] };
    const s = data.status_breakdown;
    const r = data.resolution_breakdown;
    return {
      outer: [
        { name: "Failed", value: s.failed, color: STATUS_COLORS.failed },
        { name: "Pending", value: s.pending, color: STATUS_COLORS.pending },
        { name: "Lo Progress", value: s.lo_progress, color: STATUS_COLORS.lo_progress },
      ].filter((d) => d.value > 0),
      inner: [
        { name: "Solved", value: r.solved, color: RESOLUTION_COLORS.solved },
        { name: "Unsolved", value: r.unsolved, color: RESOLUTION_COLORS.unsolved },
      ].filter((d) => d.value > 0),
    };
  }, [data]);

  return (
    <div className="max-w-6xl mx-auto px-8 py-10 space-y-8 font-sans">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">Analytics</h1>
        <p className="text-neutral-500 text-sm mt-1">
          Error volume and resolution trends across every processed batch in the selected range.
        </p>
      </header>

      <section className="bg-white border border-neutral-200 rounded-lg p-4 shadow-sm space-y-3">
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

        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-1">
            <span className="text-xs text-neutral-400 mr-1">Group by:</span>
            {BUCKETS.map((b) => (
              <button
                key={b.key}
                type="button"
                onClick={() => setBucket(b.key)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors cursor-pointer ${
                  bucket === b.key
                    ? "bg-neutral-900 text-white"
                    : "text-neutral-500 hover:bg-neutral-100"
                }`}
              >
                {b.label}
              </button>
            ))}
          </div>

          <details
            ref={excludeDetailsRef}
            className="relative"
            onToggle={(e) => {
              if ((e.currentTarget as HTMLDetailsElement).open) {
                setDraftExcluded(excludedEntities);
              }
            }}
          >
            <summary
              className={`list-none cursor-pointer select-none px-3 py-1.5 rounded-md text-xs font-medium border transition-colors inline-flex items-center gap-1.5 ${
                excludedEntities.length > 0
                  ? "bg-red-50 border-red-200 text-red-700"
                  : "border-neutral-200 text-neutral-600 hover:bg-neutral-100"
              }`}
            >
              Exclude{excludedEntities.length > 0 ? ` (${excludedEntities.length})` : ""}
              <FiChevronDown className="text-[10px]" />
            </summary>
            <div className="absolute right-0 z-20 mt-2 w-72 max-h-96 flex flex-col bg-white border border-neutral-200 rounded-lg shadow-lg p-2">
              <p className="text-[11px] text-neutral-400 px-2 pt-1 pb-2 leading-snug">
                Pick aggregators/banks to drop from every number on this page, then click OK —
                nothing changes until you apply.
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
                      onChange={() => toggleDraftExcluded(e.entity)}
                      className="rounded border-neutral-300"
                    />
                    <span className="flex-1 text-neutral-700 truncate">{e.entity}</span>
                    <span className="text-[10px] text-neutral-400 shrink-0">
                      {ENTITY_TYPE_LABEL[e.entity_type]}
                    </span>
                  </label>
                ))}
              </div>
              <div className="flex items-center justify-between gap-2 mt-2 pt-2 border-t border-neutral-100">
                <button
                  type="button"
                  onClick={() => setDraftExcluded([])}
                  className="text-xs text-red-600 hover:bg-red-50 rounded px-2 py-1.5 cursor-pointer"
                >
                  Clear
                </button>
                <button
                  type="button"
                  onClick={applyExcluded}
                  className="text-xs font-semibold text-white bg-neutral-900 hover:bg-neutral-800 rounded px-4 py-1.5 cursor-pointer"
                >
                  OK
                </button>
              </div>
            </div>
          </details>
        </div>
      </section>

      {loading && <p className="text-neutral-400 text-sm py-10">Loading analytics…</p>}
      {error && <p className="text-red-600 text-sm py-10">{error}</p>}

      {data && !loading && !error && (
        <>
          {data.kpis.total_transactions === 0 ? (
            <div className="bg-white border border-neutral-200 rounded-lg p-10 text-center shadow-sm">
              <p className="text-neutral-800 font-medium">No batches in this range</p>
              <p className="text-neutral-400 text-sm mt-1">Try a wider date range.</p>
            </div>
          ) : (
            <>
              <section className="grid grid-cols-2 md:grid-cols-5 gap-4">
                <StatCard label="Batches Included" value={data.kpis.batches_included} />
                <StatCard label="Total Transactions" value={data.kpis.total_transactions} />
                <StatCard label="Total Errors" value={data.kpis.total_errors} />
                <StatCard label="Solved" value={data.kpis.solved} />
                <StatCard label="Resolution Rate %" value={data.kpis.resolution_rate} />
              </section>

              <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                <h2 className="text-sm font-semibold text-neutral-900 mb-4">Error Volume Over Time</h2>
                <ResponsiveContainer width="100%" height={320}>
                  <BarChart data={data.trend}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e5e5" />
                    <XAxis dataKey="bucket_label" tick={{ fontSize: 12 }} />
                    <YAxis tick={{ fontSize: 12 }} allowDecimals={false} />
                    <Tooltip formatter={(value) => nf.format(Number(value))} />
                    <Legend />
                    <Bar dataKey="failed" stackId="s" name="Failed" fill={STATUS_COLORS.failed} />
                    <Bar dataKey="pending" stackId="s" name="Pending" fill={STATUS_COLORS.pending} />
                    <Bar
                      dataKey="lo_progress"
                      stackId="s"
                      name="Lo Progress"
                      fill={STATUS_COLORS.lo_progress}
                      radius={[4, 4, 0, 0]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </section>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                  <h2 className="text-sm font-semibold text-neutral-900 mb-1">Errors &amp; Resolution</h2>
                  <p className="text-xs text-neutral-500 mb-4">
                    Outer ring: what kind of error. Inner ring: how much of it got solved.
                  </p>
                  {data.kpis.total_errors === 0 ? (
                    <p className="text-neutral-400 text-sm py-16 text-center">
                      No errors in this range.
                    </p>
                  ) : (
                    <ResponsiveContainer width="100%" height={300}>
                      <PieChart>
                        <Pie
                          data={donutData.inner}
                          dataKey="value"
                          nameKey="name"
                          innerRadius={40}
                          outerRadius={75}
                          paddingAngle={2}
                        >
                          {donutData.inner.map((entry) => (
                            <Cell key={entry.name} fill={entry.color} />
                          ))}
                        </Pie>
                        <Pie
                          data={donutData.outer}
                          dataKey="value"
                          nameKey="name"
                          innerRadius={85}
                          outerRadius={120}
                          paddingAngle={2}
                        >
                          {donutData.outer.map((entry) => (
                            <Cell key={entry.name} fill={entry.color} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(value, name) => [nf.format(Number(value)), name]} />
                        <Legend />
                      </PieChart>
                    </ResponsiveContainer>
                  )}
                </section>

                <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                  <h2 className="text-sm font-semibold text-neutral-900 mb-4">
                    Top Entities by Error Volume
                  </h2>
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={data.top_entities} layout="vertical" margin={{ left: 24 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#e5e5e5" />
                      <XAxis type="number" tick={{ fontSize: 12 }} allowDecimals={false} />
                      <YAxis type="category" dataKey="entity" tick={{ fontSize: 11 }} width={140} />
                      <Tooltip formatter={(value) => nf.format(Number(value))} />
                      <Bar dataKey="total" name="Errors" fill={ENTITY_COLOR} radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </section>
              </div>

              <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
                <h2 className="text-sm font-semibold text-neutral-900 mb-4">Top Issue Categories</h2>
                <ResponsiveContainer width="100%" height={Math.max(220, data.top_categories.length * 34)}>
                  <BarChart data={data.top_categories} layout="vertical" margin={{ left: 24 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e5e5e5" />
                    <XAxis type="number" tick={{ fontSize: 12 }} allowDecimals={false} />
                    <YAxis type="category" dataKey="category" tick={{ fontSize: 11 }} width={220} />
                    <Tooltip formatter={(value) => nf.format(Number(value))} />
                    <Bar dataKey="count" name="Count" fill={CATEGORY_COLOR} radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}
