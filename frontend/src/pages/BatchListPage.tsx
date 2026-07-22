import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { batchesApi, type Batch } from "../lib/api";

/**
 * Minimal version of your "Previous Batches" search page -- lists every
 * batch, click through to its dashboard. Date/name/aggregator/issue
 * filtering, and the download-input/download-output buttons, come once
 * report generation exists (there's nothing to download yet).
 */
export default function BatchListPage() {
  const [batches, setBatches] = useState<Batch[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    batchesApi.list().then(setBatches).finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-4xl mx-auto px-8 py-10">
      <h1 className="text-2xl font-semibold text-neutral-900 mb-6">Previous Batches</h1>

      {loading ? (
        <p className="text-neutral-400 text-sm">Loading…</p>
      ) : batches.length === 0 ? (
        <p className="text-neutral-400 text-sm">
          No batches yet — <Link to="/upload" className="underline">upload one</Link>.
        </p>
      ) : (
        <div className="space-y-2">
          {batches.map((b) => (
            <Link
              key={b.id}
              to={`/dashboard/${b.id}`}
              className="flex items-center justify-between border border-neutral-200 rounded-lg px-4 py-3 bg-white hover:border-neutral-400 transition-colors"
            >
              <div>
                <p className="font-medium text-neutral-900">{b.name}</p>
                <p className="text-sm text-neutral-400">{new Date(b.created_at).toLocaleString()}</p>
              </div>
              <span
                className={`text-xs font-medium px-2 py-1 rounded-full ${
                  b.status === "finished" ? "bg-green-50 text-green-700" : "bg-neutral-100 text-neutral-600"
                }`}
              >
                {b.status === "finished" ? "Finished" : "Open"}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
