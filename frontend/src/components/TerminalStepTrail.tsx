import { useEffect, useRef, useState } from "react";
import { FiAlertCircle, FiCheck, FiDatabase, FiSearch, FiSkipForward } from "react-icons/fi";
import type { TerminalStep } from "../lib/api";

/**
 * The write, replayed one step at a time.
 *
 * Creating four rows takes a few milliseconds, so there is nothing to watch
 * live -- but there is plenty to read. The server records each statement as it
 * runs it and this walks through that record at a readable pace: which table,
 * which id, and why it happened in that order. A skipped terminal gets a step
 * of its own, because "nothing was written for Terminal 1" is exactly the kind
 * of thing that should not be silent.
 *
 * It is a replay, not a simulation. Every row here is a statement the switch
 * actually accepted; if the transaction rolled back, the trail stops where the
 * work stopped and ends with the rollback.
 */

const STEP_MS = 620;      // between one step appearing and the next
const SETTLE_MS = 340;    // how long a step spends "working" before it resolves

function icon(step: TerminalStep) {
  if (step.status === "failed") return <FiAlertCircle />;
  if (step.status === "skipped") return <FiSkipForward />;
  if (step.kind === "lookup") return <FiSearch />;
  if (step.kind === "commit") return <FiCheck />;
  return <FiDatabase />;
}

function tone(step: TerminalStep) {
  if (step.status === "failed") return "border-red-300 bg-red-50 text-red-700";
  if (step.status === "skipped") return "border-amber-300 bg-amber-50 text-amber-700";
  if (step.kind === "commit") return "border-emerald-300 bg-emerald-50 text-emerald-700";
  return "border-neutral-300 bg-white text-neutral-500";
}

function StepRow({ step, last }: { step: TerminalStep; last: boolean }) {
  const [shown, setShown] = useState(false);
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    const a = requestAnimationFrame(() => setShown(true));
    const b = window.setTimeout(() => setSettled(true), SETTLE_MS);
    return () => {
      cancelAnimationFrame(a);
      window.clearTimeout(b);
    };
  }, []);

  return (
    <li
      className={`relative pl-9 pb-4 transition-all duration-300 ease-out ${
        shown ? "opacity-100 translate-y-0" : "opacity-0 translate-y-1"
      }`}
    >
      {!last && <span className="absolute left-[11px] top-6 bottom-0 w-px bg-neutral-200" />}

      <span
        className={`absolute left-0 top-0.5 w-6 h-6 rounded-full border flex items-center justify-center text-[11px] transition-colors duration-300 ${
          settled ? tone(step) : "border-neutral-300 bg-white text-neutral-400 animate-pulse"
        }`}
      >
        {settled ? icon(step) : <span className="w-1.5 h-1.5 rounded-full bg-neutral-400" />}
      </span>

      <p className="text-[13px] font-medium text-neutral-900 leading-5">{step.title}</p>

      {step.table && (
        <code className="inline-block mt-1 px-1.5 py-0.5 rounded bg-neutral-100 text-[11px] font-mono text-neutral-600">
          {step.table}
        </code>
      )}

      {step.detail && (
        <p className="text-[11px] font-mono text-neutral-500 mt-1 break-all">{step.detail}</p>
      )}

      {step.note && (
        <p className="text-[11px] text-neutral-400 mt-1 leading-relaxed max-w-2xl">{step.note}</p>
      )}
    </li>
  );
}

export default function TerminalStepTrail({
  steps,
  running,
  environment,
  onFinished,
}: {
  steps: TerminalStep[];
  /** True while the request is still in flight — nothing to replay yet. */
  running?: boolean;
  environment: string;
  onFinished?: () => void;
}) {
  const [revealed, setRevealed] = useState(0);
  const finished = useRef(false);

  useEffect(() => {
    setRevealed(0);
    finished.current = false;
    if (!steps.length) return;

    setRevealed(1);
    if (steps.length === 1) {
      const only = window.setTimeout(() => {
        finished.current = true;
        onFinished?.();
      }, SETTLE_MS);
      return () => window.clearTimeout(only);
    }

    let n = 1;
    const timer = window.setInterval(() => {
      n += 1;
      setRevealed(n);
      if (n >= steps.length) {
        window.clearInterval(timer);
        window.setTimeout(() => {
          if (!finished.current) {
            finished.current = true;
            onFinished?.();
          }
        }, SETTLE_MS);
      }
    }, STEP_MS);

    return () => window.clearInterval(timer);
    // onFinished is intentionally not a dependency: it would restart the replay
    // every render if the parent passes an inline function.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps]);

  if (running) {
    return (
      <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
        <p className="text-[13px] text-neutral-900 font-medium flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-neutral-900 animate-pulse" />
          Writing to {environment.toUpperCase()}…
        </p>
        <p className="text-[11px] text-neutral-400 mt-1.5">
          One transaction, four tables per terminal. Nothing exists until all of it does.
        </p>
      </section>
    );
  }

  if (!steps.length) return null;

  const visible = steps.slice(0, revealed);
  const done = revealed >= steps.length;

  return (
    <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm">
      <div className="flex items-baseline justify-between gap-3 mb-4">
        <h2 className="text-sm font-semibold text-neutral-900">What was done</h2>
        <span className="text-[11px] text-neutral-400 tabular-nums">
          {Math.min(revealed, steps.length)} / {steps.length}
        </span>
      </div>

      <ol className="relative">
        {visible.map((s, i) => (
          <StepRow key={s.n} step={s} last={i === visible.length - 1 && done} />
        ))}
      </ol>

      {!done && (
        <p className="pl-9 text-[11px] text-neutral-400 animate-pulse">working…</p>
      )}
    </section>
  );
}
