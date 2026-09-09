import { useCallback, useEffect, useRef, useState } from "react";
import { FiVolume2, FiVolumeX } from "react-icons/fi";
import type { Dispute } from "../lib/api";

/**
 * Watches the switch for disputes that were not there a moment ago, and says
 * so out loud.
 *
 * Money sitting on a merchant is time-sensitive, and the disputes page is not
 * something anyone stares at all day. A spoken alert reaches someone working
 * in another window, which a badge cannot.
 *
 * Deliberately opt-in with a button rather than on by default: browsers refuse
 * speech until the page has had a real click, so a switch that silently did
 * nothing on first load would be worse than no switch. The click that turns it
 * on is the gesture that unlocks it.
 */

const STORAGE_KEY = "disputes.voiceAlerts";
const PRIMED = "Dispute alerts on.";

function speak(text: string) {
  const synth = window.speechSynthesis;
  if (!synth) return;
  // A queued backlog would announce disputes minutes after they arrived, so
  // the newest announcement replaces whatever was still speaking.
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.rate = 0.95;
  u.pitch = 1;
  u.volume = 1;
  synth.speak(u);
}

export interface WatcherProps {
  /** Every dispute currently on the page, already filtered to what counts. */
  disputes: Dispute[];
  /** Refetch from the switch. Must not disturb what the operator is doing. */
  onRefresh: () => Promise<void>;
  /** Minutes between checks. */
  intervalMinutes?: number;
}

export default function DisputeWatcher({
  disputes, onRefresh, intervalMinutes = 10,
}: WatcherProps) {
  const [enabled, setEnabled] = useState(false);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);
  const [lastAlert, setLastAlert] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  // What we have already seen. A ref, not state: changing it must not re-render
  // and must not re-run the effect that reads it.
  const seen = useRef<Set<string> | null>(null);
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;

  useEffect(() => {
    try {
      setEnabled(localStorage.getItem(STORAGE_KEY) === "1");
    } catch {
      /* private windows throw on storage; alerts simply start off */
    }
  }, []);

  // Seed from the first load without announcing: on opening the page every
  // dispute is "new", and being read a list of forty is not an alert.
  useEffect(() => {
    if (seen.current === null && disputes.length >= 0) {
      seen.current = new Set(disputes.map((d) => String(d.id ?? "")));
    }
  }, [disputes]);

  const check = useCallback(async () => {
    setChecking(true);
    try {
      await onRefresh();
      setLastCheck(new Date());
    } finally {
      setChecking(false);
    }
  }, [onRefresh]);

  // Announce anything in the new data that was not in the old.
  useEffect(() => {
    if (seen.current === null) return;
    const known = seen.current;
    const fresh = disputes.filter((d) => !known.has(String(d.id ?? "")));
    if (!fresh.length) return;

    for (const d of fresh) known.add(String(d.id ?? ""));

    const total = fresh.reduce((s, d) => s + d.amount, 0);
    const risky = fresh.filter((d) => d.double_pay_risk).length;
    const line =
      fresh.length === 1
        ? `New dispute incoming. ${fresh[0].mapped_partner}, ` +
          `${Math.round(fresh[0].amount).toLocaleString("en-NP")} rupees.`
        : `${fresh.length} new disputes incoming, ` +
          `totalling ${Math.round(total).toLocaleString("en-NP")} rupees.`;
    const warn = risky
      ? ` ${risky === 1 ? "One needs" : `${risky} need`} verification before retry.`
      : "";

    setLastAlert(`${line}${warn}`);
    if (enabledRef.current) speak(line + warn);
  }, [disputes]);

  useEffect(() => {
    const ms = Math.max(1, intervalMinutes) * 60_000;
    const t = setInterval(() => void check(), ms);
    return () => clearInterval(t);
  }, [check, intervalMinutes]);

  const toggle = () => {
    const next = !enabled;
    setEnabled(next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
    } catch {
      /* preference just will not persist */
    }
    // Speaking here, inside the click, is what unlocks audio for the tab.
    if (next) speak(PRIMED);
    else window.speechSynthesis?.cancel();
  };

  const supported = typeof window !== "undefined" && "speechSynthesis" in window;

  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px] text-neutral-500">
      <button
        type="button"
        onClick={toggle}
        disabled={!supported}
        title={
          supported
            ? enabled
              ? "Announcing new disputes out loud. Click to silence."
              : "Say new disputes out loud when they arrive"
            : "This browser cannot speak"
        }
        className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded border font-semibold transition-colors disabled:opacity-40 cursor-pointer ${
          enabled
            ? "border-emerald-300 bg-emerald-50 text-emerald-800"
            : "border-neutral-300 text-neutral-600 hover:border-neutral-400"
        }`}
      >
        {enabled ? <FiVolume2 /> : <FiVolumeX />}
        {enabled ? "Voice alerts on" : "Voice alerts off"}
      </button>

      <span>
        Checking the switch every {intervalMinutes} min
        {checking && " — checking now…"}
        {!checking && lastCheck && ` · last checked ${lastCheck.toLocaleTimeString()}`}
      </span>

      {lastAlert && (
        <span className="text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-0.5">
          {lastAlert}
        </span>
      )}
    </div>
  );
}
