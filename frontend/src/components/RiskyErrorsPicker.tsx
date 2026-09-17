import { useEffect, useMemo, useRef, useState } from "react";
import { FiAlertTriangle, FiChevronDown, FiPlus, FiX } from "react-icons/fi";
import { disputesApi, type DisputeErrorType } from "../lib/api";

/**
 * Which errors are worth checking with the partner before retrying.
 *
 * "Connection reset" and "connection was closed" used to be the whole answer,
 * written into the code. They are not the whole answer: the switch produces
 * wording nobody has seen before, and an error that means "the far end may
 * already have paid out" is a judgement about that partner's behaviour, not a
 * constant. So the list is built from the errors actually in the window, with
 * how many rows and how much held money each one accounts for — held money is
 * the part that decides whether an error is dangerous, so it is on screen next
 * to the tick.
 *
 * Add error is for one that has not turned up yet: a partner warning you about
 * a new failure mode, or an error seen on a day outside the current filter. It
 * is matched as a substring, lowercased, the same way the built-in pair always
 * was.
 *
 * Saving writes the same setting the Settings page edits and re-syncs the
 * classification rules, so the batch flow agrees with this screen about which
 * errors are dangerous.
 */

export default function RiskyErrorsPicker({
  errorTypes,
  onSaved,
}: {
  errorTypes: DisputeErrorType[];
  /** Re-read the disputes so the risk flags and counts move with the change. */
  onSaved: () => void;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  const [draft, setDraft] = useState<string[]>([]);
  const [added, setAdded] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // What is risky right now, as patterns. An error the operator ticked is
  // stored as its own text; one matched by a shorter pattern ("connection
  // reset" matching "Connection reset by peer") keeps that shorter pattern, so
  // unticking removes the rule that was actually doing the work.
  const current = useMemo(() => {
    const out = new Set<string>();
    for (const e of errorTypes) {
      if (!e.risky) continue;
      for (const m of e.matched_by.length ? e.matched_by : [e.pattern]) out.add(m);
    }
    return [...out];
  }, [errorTypes]);

  const riskyCount = errorTypes.filter((e) => e.risky).length;

  const reset = () => {
    setDraft(current);
    setAdded([]);
    setTyped("");
    setError(null);
  };

  useEffect(() => {
    if (!ref.current?.open) setDraft(current);
  }, [current]);

  // An error is ticked when one of the patterns standing against it is in the
  // draft. Ticking adds the error's own text; unticking drops every pattern
  // that matches it, otherwise a broader rule would tick it straight back on.
  const isOn = (e: DisputeErrorType) =>
    draft.some((p) => e.pattern.includes(p));

  const toggle = (e: DisputeErrorType) => {
    setDraft((prev) =>
      isOn(e)
        ? prev.filter((p) => !e.pattern.includes(p))
        : [...prev, e.pattern]
    );
  };

  const addTyped = () => {
    const text = typed.trim().toLowerCase().replace(/[,;]/g, " ").replace(/\s+/g, " ");
    if (!text) return;
    if (!draft.includes(text)) setDraft((prev) => [...prev, text]);
    if (!errorTypes.some((e) => e.pattern === text)) {
      setAdded((prev) => (prev.includes(text) ? prev : [...prev, text]));
    }
    setTyped("");
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await disputesApi.setRiskyErrors(draft);
      ref.current?.removeAttribute("open");
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save");
    } finally {
      setSaving(false);
    }
  };

  // Patterns typed in this session that match nothing on screen, plus any
  // configured pattern the window did not produce. Both are real rules and
  // both need somewhere to be unticked from.
  const extras = useMemo(() => {
    const known = new Set(errorTypes.map((e) => e.pattern));
    return [...new Set([...added, ...draft])].filter((p) => !known.has(p));
  }, [added, draft, errorTypes]);

  return (
    <details
      ref={ref}
      className="relative"
      onToggle={(e) => {
        if ((e.currentTarget as HTMLDetailsElement).open) reset();
      }}
    >
      <summary
        className={`list-none cursor-pointer select-none px-3 py-2 rounded text-xs font-semibold border transition-colors inline-flex items-center gap-1.5 ${
          riskyCount > 0
            ? "bg-amber-50 border-amber-200 text-amber-800"
            : "border-neutral-300 text-neutral-700 hover:border-neutral-400"
        }`}
      >
        <FiAlertTriangle className="text-[11px]" />
        Risky errors{riskyCount > 0 ? ` (${riskyCount})` : ""}
        <FiChevronDown className="text-[10px]" />
      </summary>

      <div className="absolute right-0 z-20 mt-2 w-[30rem] max-h-[30rem] flex flex-col bg-white border border-neutral-200 rounded-lg shadow-lg p-2">
        <p className="text-[11px] text-neutral-400 px-2 pt-1 pb-2 leading-snug">
          Tick the errors where the partner may already have paid out, so retrying
          could pay the merchant twice. These are the errors in the current window;
          held money is beside each one, because that is what makes an error
          dangerous. Saving also tells the batch flow, so the two agree.
        </p>

        <div className="overflow-y-auto">
          {errorTypes.length === 0 && (
            <p className="text-xs text-neutral-400 px-2 py-3">
              No errors in range yet. Refresh, or add one below.
            </p>
          )}

          {errorTypes.map((e) => (
            <label
              key={e.pattern}
              className="flex items-start gap-2 px-2 py-1.5 rounded hover:bg-neutral-50 text-sm cursor-pointer"
            >
              <input
                type="checkbox"
                checked={isOn(e)}
                onChange={() => toggle(e)}
                className="mt-1 rounded border-neutral-300"
              />
              <span className="flex-1 min-w-0">
                <span className="block text-neutral-700 text-[13px] leading-snug break-words">
                  {e.label}
                </span>
                <span className="block text-[11px] text-neutral-400 tabular-nums">
                  {e.count.toLocaleString()} row{e.count === 1 ? "" : "s"}
                  {e.held_count > 0 && (
                    <span className="text-amber-700">
                      {" · "}{e.held_count} holding money
                    </span>
                  )}
                  {" · NPR "}
                  {e.amount.toLocaleString(undefined, { maximumFractionDigits: 0 })}
                  {!e.seen && " · not seen in this window"}
                </span>
              </span>
            </label>
          ))}

          {extras.map((p) => (
            <label
              key={p}
              className="flex items-start gap-2 px-2 py-1.5 rounded hover:bg-neutral-50 text-sm cursor-pointer"
            >
              <input
                type="checkbox"
                checked={draft.includes(p)}
                onChange={() =>
                  setDraft((prev) =>
                    prev.includes(p) ? prev.filter((x) => x !== p) : [...prev, p]
                  )
                }
                className="mt-1 rounded border-neutral-300"
              />
              <span className="flex-1 min-w-0">
                <span className="block text-neutral-700 text-[13px] break-words">{p}</span>
                <span className="block text-[11px] text-neutral-400">
                  added by hand · matches nothing in this window yet
                </span>
              </span>
              <button
                type="button"
                onClick={(ev) => {
                  ev.preventDefault();
                  setDraft((prev) => prev.filter((x) => x !== p));
                  setAdded((prev) => prev.filter((x) => x !== p));
                }}
                className="text-neutral-300 hover:text-red-500 mt-1 cursor-pointer"
                title="Remove"
              >
                <FiX />
              </button>
            </label>
          ))}
        </div>

        <div className="flex items-center gap-2 pt-2 mt-1 border-t border-neutral-100">
          <input
            value={typed}
            onChange={(ev) => setTyped(ev.target.value)}
            onKeyDown={(ev) => {
              if (ev.key === "Enter") {
                ev.preventDefault();
                addTyped();
              }
            }}
            placeholder="An error not in the list yet…"
            className="flex-1 min-w-0 border border-neutral-300 rounded px-2 py-1.5 text-xs"
          />
          <button
            type="button"
            onClick={addTyped}
            disabled={!typed.trim()}
            className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded border border-neutral-300 hover:border-neutral-400 text-neutral-700 text-xs font-semibold disabled:opacity-40 cursor-pointer"
          >
            <FiPlus /> Add error
          </button>
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving}
            className="px-3 py-1.5 rounded bg-neutral-900 hover:bg-neutral-800 text-white text-xs font-semibold disabled:opacity-50 cursor-pointer"
          >
            {saving ? "Saving…" : "OK"}
          </button>
        </div>

        {error && <p className="text-[11px] text-red-600 px-2 pt-1.5">{error}</p>}
      </div>
    </details>
  );
}
