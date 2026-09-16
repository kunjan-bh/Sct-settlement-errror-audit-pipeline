import { useCallback, useEffect, useState } from "react";
import { FiAlertTriangle, FiCheckCircle, FiPlus, FiSearch } from "react-icons/fi";
import {
  ApiError,
  environmentApi,
  terminalsApi,
  type TerminalPlan,
  type TerminalResult,
  type TerminalStep,
} from "../lib/api";
import TerminalStepTrail from "../components/TerminalStepTrail";

/**
 * Add QR terminals to a merchant.
 *
 * The only screen in this application that changes the switch rather than
 * reporting on it, so it is deliberately a two-step: see exactly what would be
 * created, then create it. On live it also asks for the MID to be typed back,
 * because putting terminals on the wrong merchant is not something you can
 * quietly undo.
 *
 * Every field except the name is copied from a terminal the merchant already
 * has. The things that decide how money moves -- processor, payment modes,
 * MCC, allowed transaction types -- are taken from a row the switch already
 * accepted rather than guessed at here.
 *
 * Afterwards it walks through what it did, one table at a time, including the
 * names it skipped. Four rows appearing silently on a live payment switch is
 * not something anyone should have to take on trust.
 */

export default function AddTerminalPage() {
  const [mid, setMid] = useState("");
  const [count, setCount] = useState(1);
  const [names, setNames] = useState("");
  const [plan, setPlan] = useState<TerminalPlan | null>(null);
  const [env, setEnv] = useState("live");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TerminalResult | null>(null);
  const [steps, setSteps] = useState<TerminalStep[]>([]);
  const [replayed, setReplayed] = useState(false);

  useEffect(() => {
    environmentApi.get().then((d) => setEnv(d.active)).catch(() => setEnv("live"));
  }, []);

  const isLive = env === "live";

  const loadPlan = useCallback(async () => {
    if (!mid.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    setSteps([]);
    try {
      const list = names.split(",").map((n) => n.trim()).filter(Boolean);
      setPlan(await terminalsApi.plan(mid.trim(), count, list.length ? list : undefined));
    } catch (e) {
      setPlan(null);
      setError(e instanceof Error ? e.message : "Could not build a plan");
    } finally {
      setLoading(false);
    }
  }, [mid, count, names]);

  const create = async () => {
    if (!plan) return;
    setCreating(true);
    setError(null);
    setSteps([]);
    setReplayed(false);
    try {
      const r = await terminalsApi.create({
        mid: plan.mid,
        names: plan.terminals.map((t) => t.name),
        environment: plan.environment,
        confirm: confirm.trim() || undefined,
      });
      setResult(r);
      setSteps(r.steps ?? []);
      setPlan(null);
      setConfirm("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create the terminals");
      // A failed write still has a story: which tables it got through before
      // the switch refused, and that all of it was rolled back.
      if (e instanceof ApiError && Array.isArray(e.body.steps)) {
        setSteps(e.body.steps as TerminalStep[]);
      }
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="max-w-5xl mx-auto px-8 py-10 space-y-6 font-sans">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">Add Terminal</h1>
        <p className="text-neutral-500 text-sm mt-1 max-w-3xl leading-relaxed">
          Adds QR terminals to a merchant. Each one is four rows — an outlet, a PAG, a
          PAP and its notification mapping — copied from an existing terminal for
          everything except the name. This writes to the switch; everywhere else in
          this app only reads.
        </p>
      </header>

      {isLive && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 flex gap-3">
          <FiAlertTriangle className="mt-0.5 shrink-0" />
          <p>
            You are on <strong>LIVE</strong>. Terminals created here are real and appear on a
            real merchant. Switch to UAT in the nav bar to practise.
          </p>
        </div>
      )}

      <section className="bg-white border border-neutral-200 rounded-lg px-4 py-3 shadow-sm">
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs text-neutral-600">
            Merchant ID
            <input value={mid} onChange={(e) => { setMid(e.target.value); setPlan(null); }}
              placeholder="083000000002524"
              className="block mt-1 w-56 border border-neutral-300 rounded px-2 py-1.5 text-sm font-mono" />
          </label>
          <label className="text-xs text-neutral-600">
            How many
            <input type="number" min={1} max={50} value={count}
              onChange={(e) => { setCount(Math.max(1, Math.min(50, Number(e.target.value) || 1))); setPlan(null); }}
              className="block mt-1 w-24 border border-neutral-300 rounded px-2 py-1.5 text-sm" />
          </label>
          <label className="text-xs text-neutral-600 flex-1 min-w-56">
            Names <span className="text-neutral-400">(optional, comma-separated)</span>
            <input value={names} onChange={(e) => { setNames(e.target.value); setPlan(null); }}
              placeholder="left blank: Terminal 1, Terminal 2 …"
              className="block mt-1 w-full border border-neutral-300 rounded px-2 py-1.5 text-sm" />
          </label>
          <button type="button" onClick={() => void loadPlan()} disabled={!mid.trim() || loading}
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white text-xs font-semibold disabled:opacity-50 cursor-pointer">
            <FiSearch /> {loading ? "Checking…" : "Preview"}
          </button>
        </div>
        {error && <p className="text-red-600 text-xs mt-3">{error}</p>}
      </section>

      {plan && (
        <>
          <section className="bg-white border border-neutral-200 rounded-lg p-5 shadow-sm space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-neutral-900">
                Copying this merchant's existing terminal
              </h2>
              <p className="text-[11px] text-neutral-500 mt-0.5">
                Taken from this merchant's newest terminal where the outlet, PAG and PAP
                all agree. Only the name differs on the new ones — it becomes the outlet
                title, the PAG name, the PAP's TID and the notification row's device
                serial number.
              </p>
            </div>
            <dl className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs">
              {Object.entries(plan.template).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-neutral-500">{k.replace(/_/g, " ")}</dt>
                  <dd className="text-neutral-900 font-medium break-all">
                    {typeof v === "object" ? JSON.stringify(v) : String(v ?? "—")}
                  </dd>
                </div>
              ))}
            </dl>
            {!!plan.skipped?.length && (
              <p className="text-[11px] text-amber-700">
                Already taken, so {plan.skipped.length === 1 ? "it" : "they"} will be
                skipped: {plan.skipped.join(", ")}
              </p>
            )}
            {!!plan.existing_terminals.length && (
              <p className="text-[11px] text-neutral-500">
                Already has {plan.existing_terminals.length}:{" "}
                {plan.existing_terminals.slice(0, 8).join(", ")}
                {plan.existing_terminals.length > 8 ? " …" : ""}
              </p>
            )}
          </section>

          <section className="bg-white border border-neutral-200 rounded-lg shadow-sm overflow-hidden">
            <div className="px-5 pt-4 pb-2">
              <h2 className="text-sm font-semibold text-neutral-900">
                Will create {plan.terminals.length} terminal
                {plan.terminals.length === 1 ? "" : "s"} on{" "}
                <span className={plan.environment === "live" ? "text-amber-700" : "text-neutral-700"}>
                  {plan.environment.toUpperCase()}
                </span>
              </h2>
            </div>
            <table className="w-full text-xs">
              <thead>
                <tr className="bg-neutral-50 text-neutral-500">
                  <th className="text-left font-semibold px-5 py-2">Name / TID</th>
                  <th className="text-left font-semibold px-3 py-2">New PAG / PAP id</th>
                  <th className="text-left font-semibold px-3 py-2">New outlet id</th>
                  <th className="text-left font-semibold px-3 py-2">Notification</th>
                </tr>
              </thead>
              <tbody>
                {plan.terminals.map((t) => (
                  <tr key={t.id} className="border-t border-neutral-100">
                    <td className="px-5 py-1.5 font-medium text-neutral-900">{t.name}</td>
                    <td className="px-3 py-1.5 font-mono text-neutral-500">{t.id}</td>
                    <td className="px-3 py-1.5 font-mono text-neutral-500">{t.outlet_id}</td>
                    <td className="px-3 py-1.5 text-neutral-500">
                      {plan.notification_code}
                      <span className="block font-mono text-[10px] text-neutral-400">
                        serial {t.name}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="px-5 py-4 border-t border-neutral-100 flex flex-wrap items-end gap-3">
              {plan.environment === "live" && (
                <label className="text-xs text-amber-900">
                  Type the MID to confirm
                  <input value={confirm} onChange={(e) => setConfirm(e.target.value)}
                    placeholder={plan.mid}
                    className="block mt-1 w-56 border border-amber-300 rounded px-2 py-1.5 text-sm font-mono" />
                </label>
              )}
              <button type="button" onClick={() => void create()}
                disabled={creating || (plan.environment === "live" && confirm.trim() !== plan.mid)}
                className={`inline-flex items-center gap-1.5 px-4 py-2 rounded text-white text-xs font-semibold disabled:opacity-50 cursor-pointer ${
                  plan.environment === "live"
                    ? "bg-amber-600 hover:bg-amber-700"
                    : "bg-neutral-900 hover:bg-neutral-800"
                }`}>
                <FiPlus />
                {creating ? "Creating…" : `Create on ${plan.environment.toUpperCase()}`}
              </button>
            </div>
          </section>
        </>
      )}

      {(creating || steps.length > 0) && (
        <TerminalStepTrail
          steps={steps}
          running={creating}
          environment={plan?.environment ?? env}
          onFinished={() => setReplayed(true)}
        />
      )}

      {result && replayed && (
        <section className="rounded-lg border border-emerald-200 bg-emerald-50 p-5">
          <h2 className="text-sm font-semibold text-emerald-900 flex items-center gap-2">
            <FiCheckCircle /> Created {result.count} terminal
            {result.count === 1 ? "" : "s"} on {result.environment.toUpperCase()}
          </h2>
          <ul className="mt-2 space-y-1 text-xs text-emerald-900">
            {result.created.map((t) => (
              <li key={t.id}>
                <span className="font-medium">{t.name}</span>{" "}
                <span className="font-mono opacity-70">{t.id}</span>
              </li>
            ))}
          </ul>
          {!!result.skipped?.length && (
            <p className="mt-3 text-xs text-emerald-900/70">
              Skipped {result.skipped.join(", ")} — the merchant already had
              {result.skipped.length === 1 ? " that name" : " those names"}.
            </p>
          )}
        </section>
      )}
    </div>
  );
}
