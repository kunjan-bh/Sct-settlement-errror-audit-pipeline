import { useEffect, useState } from "react";
import { FiMail, FiSend, FiX } from "react-icons/fi";
import { entityEmailApi, type EntityEmailDraft } from "../lib/api";

/**
 * Compose the note to an aggregator listing which of their settlements were
 * reprocessed rather than settled in real time.
 *
 * Everything is editable before it goes, and what is on screen is exactly what
 * is sent -- the server does not re-derive the body, so an operator who
 * rewrites a sentence gets the sentence they wrote.
 */

const RECIPIENT_KEY = "settlementType.entityRecipients";

/** Aggregator addresses are not something this system stores, so the last one
 *  used for each is remembered here rather than retyped every time. */
function rememberedFor(entity: string): string {
  try {
    return JSON.parse(localStorage.getItem(RECIPIENT_KEY) || "{}")[entity] ?? "";
  } catch {
    return "";
  }
}

function remember(entity: string, to: string) {
  try {
    const all = JSON.parse(localStorage.getItem(RECIPIENT_KEY) || "{}");
    all[entity] = to;
    localStorage.setItem(RECIPIENT_KEY, JSON.stringify(all));
  } catch {
    /* not worth surfacing; the address just will not be remembered */
  }
}

export default function EntityEmailOverlay({
  entity, from, to, onClose,
}: {
  entity: string; from: string; to: string; onClose: () => void;
}) {
  const [draft, setDraft] = useState<EntityEmailDraft | null>(null);
  const [recipient, setRecipient] = useState("");
  const [cc, setCc] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    entityEmailApi
      .preview(entity, from, to)
      .then((d) => {
        if (cancelled) return;
        setDraft(d);
        setSubject(d.subject);
        setBody(d.body_html);
        setRecipient(rememberedFor(entity));
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Could not build the email"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [entity, from, to]);

  const send = async () => {
    if (!draft) return;
    setSending(true);
    setError(null);
    try {
      const r = await entityEmailApi.send({
        to: recipient, cc, subject, body_html: body,
        from_addr: draft.from_addr, from_name: draft.from_name,
        signature_html: draft.signature_html,
      });
      remember(entity, recipient);
      setSent(r.sent_to);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-start justify-center overflow-y-auto py-10 px-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-4xl">
        <div className="flex items-center gap-3 px-5 py-3 border-b border-neutral-200">
          <FiMail className="text-neutral-400" />
          <h2 className="text-sm font-semibold text-neutral-900">
            Email {entity} about reprocessed settlements
          </h2>
          <button type="button" onClick={onClose} aria-label="Close"
            className="ml-auto p-1 text-neutral-400 hover:text-neutral-800 cursor-pointer">
            <FiX />
          </button>
        </div>

        {loading && <p className="px-5 py-10 text-sm text-neutral-400">Building the email…</p>}

        {!loading && draft && !sent && (
          <div className="p-5 space-y-3">
            <p className="text-xs text-neutral-500">
              {draft.count.toLocaleString()} settlements ·{" "}
              NPR {draft.amount.toLocaleString("en-NP", { minimumFractionDigits: 2 })} ·{" "}
              {Object.entries(draft.breakdown).map(([k, n]) => `${n} ${k}`).join(", ") || "none"}
            </p>

            {!draft.count && (
              <p className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                Nothing was reprocessed for {entity} in this range — there may be no reason to send this.
              </p>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <label className="text-xs text-neutral-600">
                To
                <input value={recipient} onChange={(e) => setRecipient(e.target.value)}
                  placeholder="their team's address, comma-separated"
                  className="block w-full mt-1 border border-neutral-300 rounded px-2 py-1.5 text-sm" />
              </label>
              <label className="text-xs text-neutral-600">
                Cc
                <input value={cc} onChange={(e) => setCc(e.target.value)}
                  className="block w-full mt-1 border border-neutral-300 rounded px-2 py-1.5 text-sm" />
              </label>
            </div>

            <label className="text-xs text-neutral-600 block">
              Subject
              <input value={subject} onChange={(e) => setSubject(e.target.value)}
                className="block w-full mt-1 border border-neutral-300 rounded px-2 py-1.5 text-sm" />
            </label>

            <div>
              <p className="text-xs text-neutral-600 mb-1">Message</p>
              {/* Shown as it will arrive, and editable as text underneath: the
                  table is the point of this mail, and a raw-HTML-only editor
                  would hide the one thing worth checking. */}
              <div className="border border-neutral-200 rounded p-4 max-h-72 overflow-y-auto bg-white"
                dangerouslySetInnerHTML={{ __html: body }} />
              <details className="mt-2">
                <summary className="text-[11px] text-neutral-500 cursor-pointer select-none">
                  Edit the message
                </summary>
                <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={10}
                  className="w-full mt-1 border border-neutral-300 rounded px-2 py-1.5 text-xs font-mono" />
              </details>
            </div>

            {error && <p className="text-sm text-red-600">{error}</p>}
            {!draft.smtp_configured && (
              <p className="text-xs text-amber-700">
                No mail server configured — set SMTP_HOST in backend/.env first.
              </p>
            )}

            <div className="flex items-center gap-3 pt-1">
              <button type="button" onClick={() => void send()}
                disabled={sending || !recipient.trim() || !draft.smtp_configured}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white text-xs font-semibold disabled:opacity-50 cursor-pointer">
                <FiSend /> {sending ? "Sending…" : "Send"}
              </button>
              <button type="button" onClick={onClose}
                className="text-xs text-neutral-500 hover:text-neutral-800 cursor-pointer">Cancel</button>
            </div>
          </div>
        )}

        {sent && (
          <div className="p-8 text-center space-y-3">
            <p className="text-sm text-emerald-800 font-semibold">Sent to {sent.join(", ")}.</p>
            <button type="button" onClick={onClose}
              className="px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white text-xs font-semibold cursor-pointer">
              Close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
