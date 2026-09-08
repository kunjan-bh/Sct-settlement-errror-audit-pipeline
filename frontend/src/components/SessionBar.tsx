import { useCallback, useEffect, useState } from "react";
import { FiPlay, FiSquare, FiClock, FiCheckCircle } from "react-icons/fi";
import { sessionsApi, type Session, type SessionActivity } from "../lib/api";

/**
 * The office session strip.
 *
 * A session is the batch. It opens when someone sits down to work, quietly
 * collects every dispute decision made while it is open, and closes at the end
 * of the day with the report of what was handled. Nothing is filed by hand --
 * working the disputes list is what fills the batch in.
 *
 * Sits directly above the disputes list it records, so starting a session,
 * working it, and closing it are one motion in one place.
 */

const money = (n: number) => `NPR ${Math.round(n).toLocaleString("en-NP")}`;

export default function SessionBar({ onChanged }: { onChanged?: () => void } = {}) {
  const [session, setSession] = useState<Session | null>(null);
  const [activity, setActivity] = useState<SessionActivity["totals"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [closing, setClosing] = useState(false);
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [justClosed, setJustClosed] = useState<{ name: string; id: number } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const s = await sessionsApi.current();
      setSession(s);
      setActivity(s ? (await sessionsApi.activity(s.id)).totals : null);
    } catch {
      /* the strip is ambient -- a failed poll should not shout at anyone */
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  // Decisions are made on another page, so poll rather than thread a callback
  // through every component that can record one.
  useEffect(() => {
    const t = setInterval(() => void refresh(), 15000);
    return () => clearInterval(t);
  }, [refresh]);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      setJustClosed(null);
      setSession(await sessionsApi.start());
      await refresh();
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the session");
    } finally {
      setBusy(false);
    }
  };

  const close = async () => {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      await sessionsApi.close(session.id, notes.trim() || undefined);
      setJustClosed({ name: session.name, id: session.id });
      setSession(null);
      setActivity(null);
      setNotes("");
      setClosing(false);
      onChanged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not close the session");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-white border border-neutral-200 rounded-lg shadow-sm">
      <div className="px-4 py-2.5 flex flex-wrap items-center gap-3 text-xs">
        {session ? (
          <>
            <span className="inline-flex items-center gap-1.5 font-semibold text-emerald-800">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse" />
              {session.name}
            </span>
            <span className="text-neutral-400">·</span>
            <span className="text-neutral-600">
              {activity
                ? `${activity.handled} handled — ${activity.solved} solved, ${activity.in_progress} in progress`
                : "no decisions yet"}
            </span>
            {!!activity?.amount_handled && (
              <span className="text-neutral-500 tabular-nums">{money(activity.amount_handled)}</span>
            )}

            {closing ? (
              <div className="flex flex-wrap items-center gap-2 ml-auto">
                <input
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Notes for the report / email (optional)"
                  className="border border-neutral-300 rounded px-2 py-1 text-xs w-72"
                  autoFocus
                />
                <button type="button" onClick={() => void close()} disabled={busy}
                  className="px-3 py-1.5 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold disabled:opacity-50 cursor-pointer">
                  {busy ? "Closing…" : "Close & generate report"}
                </button>
                <button type="button" onClick={() => setClosing(false)}
                  className="text-neutral-500 hover:text-neutral-800 cursor-pointer">Cancel</button>
              </div>
            ) : (
              <button type="button" onClick={() => setClosing(true)}
                className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-neutral-300 hover:border-neutral-400 font-semibold text-neutral-700 cursor-pointer">
                <FiSquare className="text-[11px]" /> Close session
              </button>
            )}
          </>
        ) : justClosed ? (
          <>
            <span className="inline-flex items-center gap-1.5 text-emerald-800 font-semibold">
              <FiCheckCircle /> {justClosed.name} closed
            </span>
            <a href={`/dashboard/${justClosed.id}`} className="text-neutral-600 underline underline-offset-2 hover:text-neutral-900">
              Open its report to review and email it
            </a>
            <button type="button" onClick={() => void start()} disabled={busy}
              className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold disabled:opacity-50 cursor-pointer">
              <FiPlay className="text-[11px]" /> Start another session
            </button>
          </>
        ) : (
          <>
            <span className="inline-flex items-center gap-1.5 text-neutral-500">
              <FiClock /> No session running — decisions you make will open one automatically.
            </span>
            <button type="button" onClick={() => void start()} disabled={busy}
              className="ml-auto inline-flex items-center gap-1.5 px-3 py-1.5 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold disabled:opacity-50 cursor-pointer">
              <FiPlay className="text-[11px]" /> {busy ? "Starting…" : "Start session"}
            </button>
          </>
        )}

        {error && <span className="text-red-600 w-full">{error}</span>}
      </div>
    </div>
  );
}
