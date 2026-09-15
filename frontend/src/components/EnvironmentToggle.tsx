import { useCallback, useEffect, useState } from "react";
import { FiAlertTriangle, FiDatabase } from "react-icons/fi";
import { environmentApi, type EnvOption } from "../lib/api";

/**
 * Which switch every page reads from: live or UAT.
 *
 * Server-wide rather than per browser, because the server does the querying --
 * a per-tab setting would mean one tab quietly changing what another is
 * reading.
 *
 * UAT is drawn loudly on purpose. Every number in this app is money, and a
 * report built from test data that looks exactly like a live one is a worse
 * outcome than no report at all.
 */

export default function EnvironmentToggle({ onChange }: { onChange?: () => void }) {
  const [options, setOptions] = useState<EnvOption[]>([]);
  const [active, setActive] = useState<string>("live");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const d = await environmentApi.get();
      setActive(d.active);
      setOptions(d.options);
    } catch {
      /* ambient: a failed poll should not shout */
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const pick = async (name: string) => {
    if (name === active || busy) return;
    setBusy(true);
    setError(null);
    try {
      const d = await environmentApi.set(name);
      setActive(d.active);
      setOptions(d.options);
      onChange?.();
      // Every page holds data read from the other database. Nothing on screen
      // is true any more, so reload rather than leave live figures under a UAT
      // badge.
      window.location.reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not switch");
    } finally {
      setBusy(false);
    }
  };

  const isUat = active === "uat";

  return (
    <div className="flex items-center gap-2">
      <div
        className={`inline-flex items-center rounded-md border overflow-hidden ${
          isUat ? "border-amber-400" : "border-neutral-300"
        }`}
        title={
          isUat
            ? "Reading the UAT switch — figures here are test data"
            : "Reading the live switch"
        }
      >
        {options.map((o) => {
          const on = o.name === active;
          return (
            <button
              key={o.name}
              type="button"
              disabled={busy || (!o.configured && !on)}
              onClick={() => void pick(o.name)}
              title={
                o.configured
                  ? `${o.host}/${o.database}`
                  : `${o.name.toUpperCase()} is not configured in backend/.env`
              }
              className={`px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide transition-colors disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer ${
                on
                  ? o.name === "uat"
                    ? "bg-amber-500 text-white"
                    : "bg-neutral-900 text-white"
                  : "bg-white text-neutral-500 hover:text-neutral-900"
              }`}
            >
              {o.name}
            </button>
          );
        })}
      </div>

      {isUat && (
        <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-0.5">
          <FiAlertTriangle className="text-[11px]" /> test data
        </span>
      )}

      {error && (
        <span className="inline-flex items-center gap-1 text-[11px] text-red-700 max-w-xs truncate" title={error}>
          <FiDatabase className="text-[11px]" /> {error}
        </span>
      )}
    </div>
  );
}
