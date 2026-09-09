import { useCallback, useEffect, useRef, useState } from "react";
import { FiChevronDown, FiVolume2, FiVolumeX, FiX } from "react-icons/fi";
import { disputesApi, type Dispute } from "../lib/api";

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
 *
 * Lives in the layout and fetches for itself, so it keeps watching while the
 * operator is on Partner Mapping or anywhere else. Mounted inside the disputes
 * page it was unmounted the moment they navigated away: the timer stopped, the
 * record of what it had already seen was thrown away, and coming back re-seeded
 * from scratch -- so anything that arrived while they were gone was never
 * announced, which is exactly when an alert is worth having.
 */

const STORAGE_KEY = "disputes.voiceAlerts";
const VOICE_KEY = "disputes.voiceName";
const PRIMED = "Yo man, alerts are on.";

// Voices the platform labels female, most natural first. The API does not
// expose gender, so the only way to choose one is by name -- Zira ships with
// Windows, Samantha with macOS, and the Google voices appear in Chrome when
// it is online.
const FEMALE_VOICES = [
  "google uk english female", "google us english",
  "microsoft aria", "microsoft jenny", "microsoft michelle", "microsoft ana",
  "microsoft zira", "microsoft hazel", "microsoft heera", "microsoft susan",
  "samantha", "karen", "moira", "tessa", "fiona", "victoria", "serena", "linda",
];

let chosenVoice: SpeechSynthesisVoice | null = null;

function pickVoice(): SpeechSynthesisVoice | null {
  const synth = window.speechSynthesis;
  if (!synth) return null;
  const voices = synth.getVoices();

  // An explicit choice wins: accent is a matter of which voice is installed,
  // and no heuristic can guess which one someone wants to hear all day.
  try {
    const saved = localStorage.getItem(VOICE_KEY);
    if (saved) {
      const hit = voices.find((v) => v.name === saved);
      if (hit) return hit;
    }
  } catch {
    /* storage unavailable; fall through to the default preference */
  }

  // getVoices() is empty until the engine has loaded; the voiceschanged
  // listener below re-runs this once it has.
  if (!voices.length) return null;

  const byName = (needle: string) =>
    voices.find((v) => v.name.toLowerCase().includes(needle));

  for (const name of FEMALE_VOICES) {
    const hit = byName(name);
    if (hit) return hit;
  }
  // Some platforms say so outright rather than using a first name.
  const labelled = voices.find((v) => /female/i.test(v.name));
  if (labelled) return labelled;

  // No female voice installed: an English one is still better than whatever
  // the browser defaults to, which may be in another language entirely.
  return voices.find((v) => v.lang.toLowerCase().startsWith("en")) ?? null;
}

if (typeof window !== "undefined" && window.speechSynthesis) {
  chosenVoice = pickVoice();
  window.speechSynthesis.addEventListener("voiceschanged", () => {
    chosenVoice = pickVoice();
  });
}

function speak(text: string) {
  const synth = window.speechSynthesis;
  if (!synth) return;
  // A queued backlog would announce disputes minutes after they arrived, so
  // the newest announcement replaces whatever was still speaking.
  synth.cancel();
  const u = new SpeechSynthesisUtterance(text);
  if (!chosenVoice) chosenVoice = pickVoice();
  if (chosenVoice) {
    u.voice = chosenVoice;
    u.lang = chosenVoice.lang;
  }
  // Slightly slower and a touch higher than default: these are read once,
  // across a room, over office noise.
  u.rate = 0.92;
  u.pitch = 1.1;
  u.volume = 1;
  synth.speak(u);
}

function todayWindow(): { from: string; to: string } {
  // Recomputed per check rather than fixed at mount, so a session left open
  // overnight starts watching the new day instead of yesterday's.
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const now = new Date();
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  return { from: iso(yesterday), to: iso(now) };
}

export interface WatcherProps {
  /** Minutes between checks. */
  intervalMinutes?: number;
  /** Compact rendering for the nav bar. */
  compact?: boolean;
}

export default function DisputeWatcher({
  intervalMinutes = 10, compact = false,
}: WatcherProps) {
  const [disputes, setDisputes] = useState<Dispute[]>([]);
  const [enabled, setEnabled] = useState(false);
  const [lastCheck, setLastCheck] = useState<Date | null>(null);
  const [lastAlert, setLastAlert] = useState<string | null>(null);
  const [alertAt, setAlertAt] = useState<number>(0);
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
      const { from, to } = todayWindow();
      const data = await disputesApi.list(from, to);
      // Only what is still open counts as news: a settlement that arrives
      // already reprocessed, or that someone has actioned, is not.
      setDisputes(
        data.disputes.filter(
          (d) =>
            d.op_status === "pending" &&
            !d.reprocessed_ok &&
            !d.likely_settled &&
            !(d.settled_clear && !d.negative_hold)
        )
      );
      setLastCheck(new Date());
    } catch {
      /* a failed background check is not worth interrupting anyone over --
         the next one in ten minutes will try again */
    } finally {
      setChecking(false);
    }
  }, []);

  // Announce anything in the new data that was not in the old.
  useEffect(() => {
    if (seen.current === null) return;
    const known = seen.current;
    const fresh = disputes.filter((d) => !known.has(String(d.id ?? "")));
    if (!fresh.length) return;

    for (const d of fresh) known.add(String(d.id ?? ""));

    const total = fresh.reduce((s, d) => s + d.amount, 0);
    const risky = fresh.filter((d) => d.double_pay_risk).length;

    // Spoken: the count and nothing else. It is heard once, often from across
    // the room, and amounts read aloud are neither memorable nor actionable --
    // whoever hears it is going to look at the screen anyway.
    const spoken =
      fresh.length === 1
        ? "Yo man, got a new dispute."
        : `Yo man, got ${fresh.length} new disputes.`;

    // Written: the detail, because the toast is read rather than heard.
    const written =
      fresh.length === 1
        ? `New dispute — ${fresh[0].mapped_partner}, ` +
          `${Math.round(fresh[0].amount).toLocaleString("en-NP")} rupees.`
        : `${fresh.length} new disputes — ` +
          `${Math.round(total).toLocaleString("en-NP")} rupees in total.`;
    const warn = risky
      ? ` ${risky === 1 ? "One needs" : `${risky} need`} verification before retry.`
      : "";

    setLastAlert(`${written}${warn}`);
    setAlertAt(Date.now());
    if (enabledRef.current) speak(spoken);
  }, [disputes]);

  useEffect(() => {
    void check();  // seed immediately, so the first alert is one interval away
    const ms = Math.max(1, intervalMinutes) * 60_000;
    const t = setInterval(() => void check(), ms);
    return () => clearInterval(t);
  }, [check, intervalMinutes]);

  useEffect(() => {
    if (!alertAt) return;
    const t = setTimeout(() => setAlertAt(0), 45_000);
    return () => clearTimeout(t);
  }, [alertAt]);

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

  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voiceName, setVoiceName] = useState<string>("");

  useEffect(() => {
    if (!supported) return;
    const read = () => {
      setVoices(window.speechSynthesis.getVoices());
      setVoiceName(chosenVoice?.name ?? "");
    };
    read();
    window.speechSynthesis.addEventListener("voiceschanged", read);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", read);
  }, [supported]);

  const chooseVoice = (name: string) => {
    try {
      localStorage.setItem(VOICE_KEY, name);
    } catch {
      /* choice just will not persist */
    }
    chosenVoice = window.speechSynthesis.getVoices().find((v) => v.name === name) ?? null;
    setVoiceName(name);
    // Say it in the new voice so the choice is audible, not a guess from a name.
    speak(PRIMED);
  };

  const button = (
    <button
      type="button"
      onClick={toggle}
      disabled={!supported}
      title={
        supported
          ? enabled
            ? `Announcing new disputes out loud. Checking every ${intervalMinutes} min. Click to silence.`
            : `Say new disputes out loud when they arrive. Checking every ${intervalMinutes} min.`
          : "This browser cannot speak"
      }
      className={`inline-flex items-center gap-1.5 ${compact ? "p-2" : "px-2.5 py-1"} rounded border text-[11px] font-semibold transition-colors disabled:opacity-40 cursor-pointer shrink-0 ${
        enabled
          ? "border-emerald-300 bg-emerald-50 text-emerald-800"
          : "border-neutral-300 text-neutral-600 hover:border-neutral-400"
      }`}
    >
      {enabled ? <FiVolume2 /> : <FiVolumeX />}
      {!compact && (enabled ? "Voice alerts on" : "Voice alerts off")}
      {checking && <span className="w-1 h-1 rounded-full bg-current animate-pulse" />}
    </button>
  );

  if (compact) {
    return (
      <>
        <div className="flex items-center">
          {button}
          {enabled && voices.length > 1 && (
            <details className="relative">
              <summary
                className="list-none cursor-pointer select-none px-1 py-2 text-neutral-400 hover:text-neutral-700"
                title="Choose the voice and accent"
              >
                <FiChevronDown className="text-[10px]" />
              </summary>
              <div className="absolute right-0 z-30 mt-2 w-64 max-h-72 overflow-y-auto bg-white border border-neutral-200 rounded-lg shadow-lg p-1.5">
                <p className="text-[11px] text-neutral-400 px-2 py-1 leading-snug">
                  Accent depends on which voices Windows has. Add more under
                  Settings → Time &amp; Language → Speech.
                </p>
                {voices.map((v) => (
                  <button
                    key={v.name}
                    type="button"
                    onClick={() => chooseVoice(v.name)}
                    className={`w-full text-left px-2 py-1.5 rounded text-xs cursor-pointer ${
                      v.name === voiceName
                        ? "bg-neutral-900 text-white"
                        : "text-neutral-700 hover:bg-neutral-100"
                    }`}
                  >
                    {v.name}
                    <span className={`ml-1 ${v.name === voiceName ? "text-neutral-300" : "text-neutral-400"}`}>
                      {v.lang}
                    </span>
                  </button>
                ))}
              </div>
            </details>
          )}
        </div>
        {lastAlert && alertAt > 0 && (
          <div
            role="status"
            className="fixed bottom-5 right-5 z-50 max-w-sm rounded-lg border border-amber-300 bg-amber-50 shadow-lg px-4 py-3 text-xs text-amber-900"
          >
            <div className="flex items-start gap-3">
              <FiVolume2 className="mt-0.5 shrink-0 text-amber-700" />
              <p className="leading-relaxed">{lastAlert}</p>
              <button
                type="button"
                onClick={() => setAlertAt(0)}
                aria-label="Dismiss"
                className="ml-auto -mt-1 -mr-1 p-1 text-amber-700 hover:text-amber-900 cursor-pointer"
              >
                <FiX />
              </button>
            </div>
          </div>
        )}
      </>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3 text-[11px] text-neutral-500">
      {button}

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
