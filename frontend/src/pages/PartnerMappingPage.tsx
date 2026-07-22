import { useEffect, useState } from "react";
import { partnerMappingsApi, type PartnerMapping } from "../lib/api";

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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      setMappings(await partnerMappingsApi.list());
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

      <BankWalletSection items={bankWallets} loading={loading} onChanged={refresh} />
      <AggregatorSection groups={aggregatorGroups} loading={loading} onChanged={refresh} />
    </div>
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
                    className="text-neutral-400 hover:text-red-600 text-xs"
                  >
                    Remove
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
              <div className="bg-neutral-50 px-4 py-2 text-sm font-medium text-neutral-700 flex justify-between">
                <span>{name}</span>
                <span className="text-neutral-400">{items.length} code(s)</span>
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
                      className="text-neutral-400 hover:text-red-600"
                      title="Remove"
                    >
                      ×
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
