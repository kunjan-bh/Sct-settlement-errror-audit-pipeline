import { useEffect, useState } from "react";
import { FiTrash2 } from "react-icons/fi";
import { partnerMappingsApi, type PartnerMapping, type UnmappedCode } from "../lib/api";

/**
 * The page you asked for: manage which 3-char MID member codes belong to
 * which aggregator, and which map directly to a bank/wallet.
 *
 * Two independent forms because the two buckets behave differently:
 *  - Bank/Wallet: added one row at a time (each is its own direct entity).
 *  - Aggregator: pick/create an aggregator name, paste a list of member
 *    codes (comma or newline separated -- matches how you'd paste from a
 *    spreadsheet or a JSON array), import them all at once.
 *
 * Styling is intentionally plain right now -- this proves the workflow
 * end-to-end against your real backend. The premium look (shadcn, motion,
 * etc.) gets layered on once all the pages exist, so we're not restyling
 * the same page three times.
 */
export default function PartnerMappingPage() {
  const [mappings, setMappings] = useState<PartnerMapping[]>([]);
  const [unmapped, setUnmapped] = useState<UnmappedCode[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const [rows, gaps] = await Promise.all([
        partnerMappingsApi.list(),
        partnerMappingsApi.unmapped(),
      ]);
      setMappings(rows);
      setUnmapped(gaps);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load mappings");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const bankWallets = mappings.filter((m) => m.bucket === "bank_wallet");
  const aggregatorGroups = groupByAggregator(mappings.filter((m) => m.bucket === "aggregator"));

  return (
    <div className="max-w-5xl mx-auto p-8 space-y-10">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-900">Partner Mapping</h1>
        <p className="text-neutral-500 mt-1">
          MID member code (first 3 digits) → Aggregator or Bank/Wallet. Codes not listed here resolve to "No Aggregator".
        </p>
      </header>

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 text-red-700 px-4 py-3 text-sm">
          {error}
        </div>
      )}

      <UnmappedSection items={unmapped} loading={loading} />
      <BankWalletSection items={bankWallets} loading={loading} onChanged={refresh} />
      <AggregatorSection groups={aggregatorGroups} loading={loading} onChanged={refresh} />
    </div>
  );
}

/**
 * Member codes that show up in real transactions but have no mapping, so every
 * MID under them resolves to "No Aggregator".
 *
 * The dashboard already reports how many transactions fell through; this says
 * WHICH codes to add, which otherwise needed a hand-written query. Computed
 * from the transactions rather than from the stored partner_name, so adding a
 * mapping clears the row here immediately -- partner_name is resolved once at
 * ingest and keeps saying "No Aggregator" on old batches long after the
 * mapping exists.
 */
function UnmappedSection({ items, loading }: { items: UnmappedCode[]; loading: boolean }) {
  if (loading || items.length === 0) return null;

  const total = items.reduce((sum, u) => sum + u.txn_count, 0);

  return (
    <section className="space-y-3">
      <div className="flex items-baseline gap-2">
        <h2 className="text-lg font-medium text-neutral-800">Unmapped member codes</h2>
        <span className="text-xs font-medium bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">
          {items.length}
        </span>
      </div>
      <p className="text-neutral-500 text-sm">
        Seen in transactions but not mapped below, so {total.toLocaleString()} transaction
        {total === 1 ? "" : "s"} resolved to "No Aggregator". Add each to an aggregator or
        bank/wallet to fix future batches.
      </p>

      <div className="border border-amber-200 bg-amber-50/40 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-neutral-500 border-b border-amber-200">
              <th className="px-4 py-2 font-medium">Code</th>
              <th className="px-4 py-2 font-medium text-right">Txns</th>
              <th className="px-4 py-2 font-medium">Sample MID</th>
              <th className="px-4 py-2 font-medium">Sample merchant</th>
              <th className="px-4 py-2 font-medium">Last seen</th>
            </tr>
          </thead>
          <tbody>
            {items.map((u) => (
              <tr key={u.member_code} className="border-b border-amber-100 last:border-0">
                <td className="px-4 py-2 font-mono font-semibold text-neutral-900">{u.member_code}</td>
                <td className="px-4 py-2 text-right tabular-nums text-neutral-700">
                  {u.txn_count.toLocaleString()}
                </td>
                <td className="px-4 py-2 font-mono text-xs text-neutral-600">{u.sample_mid}</td>
                <td className="px-4 py-2 text-neutral-700">{u.sample_merchant || "—"}</td>
                <td className="px-4 py-2 text-neutral-500 text-xs">
                  {u.last_seen ? u.last_seen.slice(0, 10) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function groupByAggregator(items: PartnerMapping[]) {
  const map = new Map<string, PartnerMapping[]>();
  for (const item of items) {
    const list = map.get(item.partner_name) ?? [];
    list.push(item);
    map.set(item.partner_name, list);
  }
  return Array.from(map.entries()).sort(([a], [b]) => a.localeCompare(b));
}

// ---------------------------------------------------------------------------

function BankWalletSection({
  items,
  loading,
  onChanged,
}: {
  items: PartnerMapping[];
  loading: boolean;
  onChanged: () => void;
}) {
  const [memberCode, setMemberCode] = useState("");
  const [partnerName, setPartnerName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const handleAdd = async () => {
    setFormError(null);
    if (!memberCode.trim() || !partnerName.trim()) {
      setFormError("Member code and name are both required");
      return;
    }
    setSubmitting(true);
    try {
      await partnerMappingsApi.create({
        member_code: memberCode.trim(),
        bucket: "bank_wallet",
        partner_name: partnerName.trim(),
      });
      setMemberCode("");
      setPartnerName("");
      onChanged();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Failed to add");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    await partnerMappingsApi.remove(id);
    onChanged();
  };

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-medium text-neutral-800">Bank / Wallet (direct entities)</h2>

      <div className="flex gap-2 items-start">
        <input
          className="border border-neutral-300 rounded-md px-3 py-2 w-28 text-sm"
          placeholder="007"
          value={memberCode}
          onChange={(e) => setMemberCode(e.target.value)}
          maxLength={3}
        />
        <input
          className="border border-neutral-300 rounded-md px-3 py-2 flex-1 text-sm"
          placeholder="e.g. NIC ASIA Bank Ltd"
          value={partnerName}
          onChange={(e) => setPartnerName(e.target.value)}
        />
        <button
          onClick={handleAdd}
          disabled={submitting}
          className="bg-neutral-900 text-white rounded-md px-4 py-2 text-sm font-medium disabled:opacity-50"
        >
          Add
        </button>
      </div>
      {formError && <p className="text-red-600 text-sm">{formError}</p>}

      {loading ? (
        <p className="text-neutral-400 text-sm">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-neutral-400 text-sm">No bank/wallet entities mapped yet.</p>
      ) : (
        <table className="w-full text-sm border border-neutral-200 rounded-lg overflow-hidden">
          <thead className="bg-neutral-50 text-neutral-500">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Code</th>
              <th className="text-left px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.id} className="border-t border-neutral-100">
                <td className="px-4 py-2 font-mono">{item.member_code}</td>
                <td className="px-4 py-2">{item.partner_name}</td>
                <td className="px-4 py-2 text-right">
                  <button
                    onClick={() => handleDelete(item.id)}
                    className="text-neutral-400 hover:text-red-600 p-1 cursor-pointer transition-colors"
                    title="Remove"
                  >
                    <FiTrash2 className="text-sm" />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------

function AggregatorSection({
  groups,
  loading,
  onChanged,
}: {
  groups: [string, PartnerMapping[]][];
  loading: boolean;
  onChanged: () => void;
}) {
  const [partnerName, setPartnerName] = useState("");
  const [codesText, setCodesText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  const handleImport = async () => {
    setFormError(null);
    setResult(null);

    const codes = parseCodesInput(codesText);
    if (!partnerName.trim()) {
      setFormError("Aggregator name is required");
      return;
    }
    if (codes.length === 0) {
      setFormError("Paste at least one member code (comma, newline, or JSON array)");
      return;
    }

    setSubmitting(true);
    try {
      const res = await partnerMappingsApi.bulkImport({ partner_name: partnerName.trim(), member_codes: codes });
      setResult(
        `Imported ${res.created_count} code(s).` +
          (res.skipped.length ? ` Skipped ${res.skipped.length}: ${res.skipped.map((s) => `${s.code} (${s.reason})`).join(", ")}` : "")
      );
      setCodesText("");
      onChanged();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (id: number) => {
    await partnerMappingsApi.remove(id);
    onChanged();
  };

  const handleDeleteAggregator = async (name: string) => {
    if (confirm(`Are you sure you want to delete all cooperative mappings under "${name}"?`)) {
      await partnerMappingsApi.removeAggregator(name);
      onChanged();
    }
  };

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-medium text-neutral-800">Aggregators (grouped cooperatives)</h2>

      <div className="space-y-2">
        <input
          className="border border-neutral-300 rounded-md px-3 py-2 w-full text-sm"
          placeholder="Aggregator name (new or existing)"
          value={partnerName}
          onChange={(e) => setPartnerName(e.target.value)}
        />
        <textarea
          className="border border-neutral-300 rounded-md px-3 py-2 w-full text-sm font-mono h-28"
          placeholder={'Member codes, one per line or comma-separated, e.g.\n059\n096\n18\nor ["059","096","18"]'}
          value={codesText}
          onChange={(e) => setCodesText(e.target.value)}
        />
        <button
          onClick={handleImport}
          disabled={submitting}
          className="bg-neutral-900 text-white rounded-md px-4 py-2 text-sm font-medium disabled:opacity-50"
        >
          Import codes
        </button>
        {formError && <p className="text-red-600 text-sm">{formError}</p>}
        {result && <p className="text-green-700 text-sm">{result}</p>}
      </div>

      {loading ? (
        <p className="text-neutral-400 text-sm">Loading…</p>
      ) : groups.length === 0 ? (
        <p className="text-neutral-400 text-sm">No aggregators mapped yet.</p>
      ) : (
        <div className="space-y-3">
          {groups.map(([name, items]) => (
            <div key={name} className="border border-neutral-200 rounded-lg overflow-hidden">
              <div className="bg-neutral-50 px-4 py-2 text-sm font-medium text-neutral-700 flex justify-between items-center">
                <span>{name}</span>
                <div className="flex items-center gap-3">
                  <span className="text-neutral-400 text-xs">{items.length} code(s)</span>
                  <button
                    onClick={() => handleDeleteAggregator(name)}
                    className="inline-flex items-center gap-1 text-red-600 hover:text-red-800 text-xs font-semibold cursor-pointer transition-colors"
                    title="Delete Map"
                  >
                    <FiTrash2 size={12} />
                    Delete Mapp
                  </button>
                </div>
              </div>
              <div className="px-4 py-3 flex flex-wrap gap-2">
                {items.map((item) => (
                  <span
                    key={item.id}
                    className="inline-flex items-center gap-1.5 bg-neutral-100 rounded-full px-3 py-1 text-xs font-mono"
                  >
                    {item.member_code}
                    <button
                      onClick={() => handleDelete(item.id)}
                      className="text-neutral-400 hover:text-red-600 focus:outline-none cursor-pointer transition-colors"
                      title="Remove"
                    >
                      <FiTrash2 size={10} />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** Accepts a JSON array, comma-separated, or newline-separated list. */
function parseCodesInput(text: string): string[] {
  const trimmed = text.trim();
  if (!trimmed) return [];

  if (trimmed.startsWith("[")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return parsed.map(String).map((s) => s.trim()).filter(Boolean);
    } catch {
      // fall through to comma/newline parsing
    }
  }

  return trimmed
    .split(/[\n,]/)
    .map((s) => s.trim())
    .filter(Boolean);
}
