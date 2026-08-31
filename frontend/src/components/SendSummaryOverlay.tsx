import { useEffect, useState } from "react";
import { FiSend, FiX, FiAlertTriangle } from "react-icons/fi";
import { batchEmailApi, type BatchEmailDraft } from "../lib/api";

/**
 * The editable preview shown after a batch is finished, before anything is
 * sent. Everything on screen is what actually goes out: recipients, subject
 * and body are posted back verbatim, so an edit here is not cosmetic.
 *
 * The Errors & Resolution ring is rendered server-side and arrives as a base64
 * PNG (services/chart_image.py) -- the same bytes are shown here and embedded
 * inline in the message, so the preview cannot drift from what is delivered.
 *
 * Body editing is plain HTML in a textarea rather than a rich-text editor: the
 * default body is our own markup, ops mostly tweaks wording, and a WYSIWYG
 * would need to round-trip HTML safely for no real gain here.
 */
export default function SendSummaryOverlay({
  batchId,
  onClose,
}: {
  batchId: number;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<BatchEmailDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string[] | null>(null);
  const [attached, setAttached] = useState<{ filename: string; bytes: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    batchEmailApi
      .preview(batchId)
      .then((d) => !cancelled && setDraft(d))
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to build preview"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [batchId]);

  const set = <K extends keyof BatchEmailDraft>(key: K, value: BatchEmailDraft[K]) =>
    setDraft((prev) => (prev ? { ...prev, [key]: value } : prev));

  const handleSend = async () => {
    if (!draft) return;
    setSending(true);
    setError(null);
    try {
      const r = await batchEmailApi.send(batchId, {
        subject: draft.subject,
        from_addr: draft.from_addr,
        from_name: draft.from_name,
        to: draft.to,
        cc: draft.cc,
        body_html: draft.body_html,
        chart_png_base64: draft.chart_png_base64,
        signature_html: draft.signature_html,
        attach_report: draft.attach_report,
        batch_name: draft.batch.name,
      });
      setSent(r.sent_to);
      setAttached(r.attachment);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send email");
    } finally {
      setSending(false);
    }
  };

  const field = "w-full border border-neutral-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-neutral-400";

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-neutral-900/40 backdrop-blur-[1px] overflow-y-auto p-6">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-3xl my-8">
        <header className="flex items-start justify-between gap-4 px-6 pt-6 pb-4 border-b border-neutral-100">
          <div>
            <h2 className="text-base font-semibold text-neutral-900">Send Batch Summary</h2>
            <p className="text-neutral-500 text-xs mt-1">
              Review and edit before sending — nothing has been sent yet.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="text-neutral-400 hover:text-neutral-700 cursor-pointer p-1"
            aria-label="Close"
          >
            <FiX />
          </button>
        </header>

        {loading && <p className="text-neutral-400 text-sm px-6 py-10">Building preview…</p>}

        {sent && (
          <div className="px-6 py-10 text-center">
            <p className="text-neutral-900 font-medium">Summary sent</p>
            <p className="text-neutral-500 text-sm mt-1">Delivered to {sent.join(", ")}.</p>
            {attached && (
              <p className="text-neutral-500 text-xs mt-1">
                Attached {attached.filename} ({Math.round(attached.bytes / 1024).toLocaleString()} KB).
              </p>
            )}
            <button
              type="button"
              onClick={onClose}
              className="mt-5 px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs cursor-pointer"
            >
              Done
            </button>
          </div>
        )}

        {draft && !sent && (
          <>
            <div className="px-6 py-5 space-y-3">
              {!draft.smtp_configured && (
                <div className="flex items-start gap-2 text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded px-3 py-2">
                  <FiAlertTriangle className="mt-0.5 shrink-0" />
                  <span>
                    No SMTP host is configured, so this cannot be sent yet. Set one on the{" "}
                    <a href="/settings" className="underline font-medium">
                      Settings
                    </a>{" "}
                    page.
                  </span>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <label className="text-xs text-neutral-500 space-y-1 block">
                  From
                  <input
                    className={field}
                    value={draft.from_addr}
                    onChange={(e) => set("from_addr", e.target.value)}
                  />
                </label>
                <label className="text-xs text-neutral-500 space-y-1 block">
                  From name
                  <input
                    className={field}
                    value={draft.from_name}
                    onChange={(e) => set("from_name", e.target.value)}
                  />
                </label>
                <label className="text-xs text-neutral-500 space-y-1 block">
                  To
                  <input
                    className={field}
                    value={draft.to}
                    onChange={(e) => set("to", e.target.value)}
                  />
                </label>
                <label className="text-xs text-neutral-500 space-y-1 block">
                  Cc
                  <input
                    className={field}
                    value={draft.cc}
                    placeholder="(optional)"
                    onChange={(e) => set("cc", e.target.value)}
                  />
                </label>
              </div>

              <label className="text-xs text-neutral-500 space-y-1 block">
                Subject
                <input
                  className={field}
                  value={draft.subject}
                  onChange={(e) => set("subject", e.target.value)}
                />
              </label>

              <label className="text-xs text-neutral-500 space-y-1 block">
                Body (HTML — taken from this batch's notes)
                <textarea
                  className={`${field} font-mono text-xs leading-relaxed`}
                  rows={9}
                  value={draft.body_html}
                  onChange={(e) => set("body_html", e.target.value)}
                />
              </label>

              <label className="flex items-start gap-2 text-xs text-neutral-600 cursor-pointer">
                <input
                  type="checkbox"
                  checked={draft.attach_report}
                  onChange={(e) => set("attach_report", e.target.checked)}
                  className="mt-0.5 h-4 w-4 accent-neutral-900 cursor-pointer"
                />
                <span>
                  Attach the full Excel report —{" "}
                  <span className="font-medium text-neutral-800">{draft.report_filename}</span>
                  <span className="block text-neutral-400">
                    The same workbook as Download Full Report. Built when you send, so it is not
                    previewed here.
                  </span>
                </span>
              </label>

              <div>
                <p className="text-xs text-neutral-500 mb-2">
                  Attached inline — Errors &amp; Resolution
                </p>
                {draft.chart_png_base64 ? (
                  <img
                    src={`data:image/png;base64,${draft.chart_png_base64}`}
                    alt="Errors and resolution chart"
                    className="max-w-full border border-neutral-200 rounded"
                  />
                ) : (
                  <p className="text-xs text-neutral-400 italic">
                    No outstanding errors in this batch, so no chart is attached.
                  </p>
                )}
              </div>

              <label className="text-xs text-neutral-500 space-y-1 block">
                Signature (HTML)
                <textarea
                  className={`${field} font-mono text-xs leading-relaxed`}
                  rows={4}
                  value={draft.signature_html}
                  placeholder="Set a default on the Settings page — your Outlook signature does not apply here."
                  onChange={(e) => set("signature_html", e.target.value)}
                />
              </label>

              {(draft.signature_html.trim() || draft.signature_logo_base64) && (
                <div className="border-t border-neutral-200 pt-3">
                  <p className="text-xs text-neutral-500 mb-2">Signature preview</p>
                  {/* Our own stored markup, shown so the sender sees exactly
                      what the recipient will get. */}
                  <div
                    className="text-sm text-neutral-800"
                    dangerouslySetInnerHTML={{ __html: draft.signature_html }}
                  />
                  {draft.signature_logo_base64 && (
                    <img
                      src={`data:image/png;base64,${draft.signature_logo_base64}`}
                      alt="Signature logo"
                      className="mt-2.5"
                    />
                  )}
                </div>
              )}

              {error && <p className="text-red-600 text-xs">{error}</p>}
            </div>

            <footer className="flex items-center justify-end gap-2 px-6 py-4 border-t border-neutral-100">
              <button
                type="button"
                onClick={onClose}
                disabled={sending}
                className="px-3.5 py-2 text-xs font-medium text-neutral-500 hover:text-neutral-800 cursor-pointer disabled:opacity-50"
              >
                Skip
              </button>
              <button
                type="button"
                onClick={handleSend}
                disabled={sending || !draft.smtp_configured}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs transition-colors shadow-sm disabled:opacity-50 cursor-pointer"
              >
                <FiSend className="text-sm" />
                {sending ? "Sending…" : "Send Summary"}
              </button>
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
