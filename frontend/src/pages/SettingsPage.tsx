import { useEffect, useMemo, useState } from "react";
import { FiSave, FiSend, FiMail, FiServer } from "react-icons/fi";
import { settingsApi, SECRET_MASK, type SettingField } from "../lib/api";

/**
 * Settings: the mail and SMTP configuration behind the end-of-batch summary
 * email. Every field is rendered from the schema the API returns rather than
 * hardcoded here, so adding a setting is a backend-only change (see
 * services/settings_service.py).
 *
 * The SMTP password is never sent to the browser -- it arrives as a mask, and
 * is posted back unchanged unless the user actually types a new one.
 */

const GROUPS: { key: SettingField["group"]; title: string; blurb: string; icon: typeof FiMail }[] = [
  {
    key: "mail",
    title: "Email",
    blurb:
      "Who the batch summary comes from and goes to. These are the defaults — you can still change any of them in the preview before sending.",
    icon: FiMail,
  },
  {
    key: "smtp",
    title: "SMTP Server",
    blurb:
      "The mail server used to send. Nothing can be sent until a host is set. Port 465 uses implicit SSL; anything else uses STARTTLS when enabled.",
    icon: FiServer,
  },
];

export default function SettingsPage() {
  const [schema, setSchema] = useState<SettingField[]>([]);
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    settingsApi
      .get()
      .then((d) => {
        if (cancelled) return;
        setSchema(d.schema);
        setValues(d.values);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : "Failed to load settings"))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, []);

  const grouped = useMemo(
    () => GROUPS.map((g) => ({ ...g, fields: schema.filter((f) => f.group === g.key) })),
    [schema]
  );

  const set = (key: string, value: string | number | boolean) => {
    setValues((prev) => ({ ...prev, [key]: value }));
    setNotice(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const d = await settingsApi.save(values);
      setValues(d.values);
      setSchema(d.schema);
      setNotice("Settings saved.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setError(null);
    setNotice(null);
    try {
      const r = await settingsApi.sendTest();
      setNotice(`Test email sent to ${r.sent_to.join(", ")}.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to send test email");
    } finally {
      setTesting(false);
    }
  };

  const smtpReady = Boolean(String(values.smtp_host ?? "").trim());

  return (
    <div className="max-w-3xl mx-auto px-8 py-10 space-y-8 font-sans">
      <header>
        <h1 className="text-2xl font-semibold text-neutral-900 tracking-tight">Settings</h1>
        <p className="text-neutral-500 text-sm mt-1 max-w-2xl leading-relaxed">
          Configuration for the summary email sent when a batch is finished.
        </p>
      </header>

      {loading && <p className="text-neutral-400 text-sm py-10">Loading settings…</p>}

      {!loading && (
        <>
          {grouped.map((group) => (
            <section
              key={group.key}
              className="bg-white border border-neutral-200 rounded-lg shadow-sm overflow-hidden"
            >
              <div className="px-5 pt-5 pb-3 border-b border-neutral-100">
                <h2 className="text-sm font-semibold text-neutral-900 flex items-center gap-2">
                  <group.icon className="text-neutral-400" />
                  {group.title}
                </h2>
                <p className="text-neutral-500 text-xs mt-1 leading-relaxed">{group.blurb}</p>
              </div>

              <div className="p-5 space-y-4">
                {group.fields.map((f) => (
                  <div key={f.key} className="grid grid-cols-1 sm:grid-cols-3 gap-2 sm:items-center">
                    <label htmlFor={f.key} className="text-sm text-neutral-600">
                      {f.label}
                    </label>
                    <div className="sm:col-span-2">
                      {f.type === "bool" ? (
                        <input
                          id={f.key}
                          type="checkbox"
                          checked={Boolean(values[f.key])}
                          onChange={(e) => set(f.key, e.target.checked)}
                          className="h-4 w-4 accent-neutral-900 cursor-pointer"
                        />
                      ) : (
                        <input
                          id={f.key}
                          type={f.secret ? "password" : f.type === "int" ? "number" : "text"}
                          value={String(values[f.key] ?? "")}
                          placeholder={f.default}
                          onChange={(e) => set(f.key, e.target.value)}
                          onFocus={(e) => {
                            // Clear the mask on focus so typing replaces the
                            // secret instead of appending to a row of dots.
                            if (f.secret && e.target.value === SECRET_MASK) set(f.key, "");
                          }}
                          className="w-full border border-neutral-300 rounded px-2.5 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-neutral-400"
                        />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-1.5 px-4 py-2 rounded bg-neutral-900 hover:bg-neutral-800 text-white font-semibold text-xs transition-colors shadow-sm disabled:opacity-50 cursor-pointer"
            >
              <FiSave className="text-sm" />
              {saving ? "Saving…" : "Save Settings"}
            </button>

            <button
              type="button"
              onClick={handleTest}
              disabled={testing || !smtpReady}
              title={smtpReady ? undefined : "Set an SMTP host first"}
              className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded border border-neutral-300 hover:border-neutral-400 text-neutral-700 text-xs font-semibold transition-colors disabled:opacity-50 cursor-pointer"
            >
              <FiSend className="text-sm" />
              {testing ? "Sending…" : "Send Test Email"}
            </button>

            {!smtpReady && (
              <span className="text-xs text-amber-700">
                No SMTP host set — summary emails cannot be sent yet.
              </span>
            )}
          </div>

          {notice && <p className="text-emerald-700 text-sm">{notice}</p>}
          {error && <p className="text-red-600 text-sm">{error}</p>}
        </>
      )}
    </div>
  );
}
