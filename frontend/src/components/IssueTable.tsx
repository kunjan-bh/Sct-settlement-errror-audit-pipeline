import { useState, useEffect, useRef } from "react";
import type { IssueStatusValue, MidOverride } from "../lib/api";

export type FlatIssueRow = {
  issueId: number | null;
  partnerName: string;
  category: string;
  count: number;
  affectedMids: string[];
  status: IssueStatusValue;
  comment: string | null;
  mid_overrides?: Record<string, MidOverride>;
  last_solved_comment?: string | null;
};

const STATUS_OPTIONS: { value: IssueStatusValue; label: string; style: string }[] = [
  { value: "pending", label: "Pending", style: "bg-neutral-100 text-neutral-700 border-neutral-300" },
  { value: "in_progress", label: "In Progress", style: "bg-amber-50 text-amber-800 border-amber-300" },
  { value: "solved", label: "Solved", style: "bg-emerald-50 text-emerald-800 border-emerald-300" },
  { value: "exclude", label: "Exclude", style: "bg-gray-200 text-gray-600 border-gray-300" },
];

// ─── Override MID Modal ───────────────────────────────────────────────────────
function MidOverrideModal({
  affectedMids,
  currentOverrides,
  onSave,
  onClose,
}: {
  affectedMids: string[];
  currentOverrides: Record<string, MidOverride>;
  onSave: (overrides: Record<string, MidOverride>) => Promise<void>;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(
    new Set(Object.keys(currentOverrides))
  );
  const [overrideStatus, setOverrideStatus] = useState<IssueStatusValue>("solved");
  const [remark, setRemark] = useState("");
  const [saving, setSaving] = useState(false);

  const maxAllowed = affectedMids.length - 1;

  const toggleMid = (mid: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(mid)) {
        next.delete(mid);
      } else {
        if (next.size >= maxAllowed) return prev; // enforce n-1
        next.add(mid);
      }
      return next;
    });
  };

  const handleSave = async () => {
    const newOverrides: Record<string, MidOverride> = {};
    selected.forEach((mid) => {
      newOverrides[mid] = { status: overrideStatus, remark: remark.trim() };
    });
    setSaving(true);
    try {
      await onSave(newOverrides);
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/30 backdrop-blur-[2px] flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-2xl border border-neutral-200 w-full max-w-md">
        {/* Header */}
        <div className="px-5 py-4 border-b border-neutral-100 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-neutral-900">Override MID Status</h3>
            <p className="text-xs text-neutral-400 mt-0.5">
              Select up to {maxAllowed} of {affectedMids.length} MIDs to override
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-neutral-400 hover:text-neutral-600 transition-colors text-lg leading-none cursor-pointer"
          >
            ×
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          {/* MID Selection */}
          <div>
            <p className="text-xs font-medium text-neutral-600 mb-2 uppercase tracking-wide">
              Select MIDs to Override ({selected.size}/{maxAllowed} max)
            </p>
            <div className="border border-neutral-200 rounded-lg max-h-48 overflow-y-auto divide-y divide-neutral-100">
              {affectedMids.map((mid) => {
                const isChecked = selected.has(mid);
                const isDisabled = !isChecked && selected.size >= maxAllowed;
                return (
                  <label
                    key={mid}
                    className={`flex items-center gap-3 px-3 py-2 cursor-pointer transition-colors ${
                      isDisabled ? "opacity-40 cursor-not-allowed" : "hover:bg-neutral-50"
                    } ${isChecked ? "bg-red-50" : ""}`}
                  >
                    <input
                      type="checkbox"
                      checked={isChecked}
                      disabled={isDisabled}
                      onChange={() => toggleMid(mid)}
                      className="rounded border-neutral-300 text-red-500 focus:ring-red-400 cursor-pointer"
                    />
                    <span
                      className={`text-xs font-mono ${
                        isChecked ? "text-red-600 font-medium" : "text-neutral-700"
                      }`}
                    >
                      {mid}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>

          {/* Override Status */}
          <div>
            <label className="text-xs font-medium text-neutral-600 mb-1.5 block uppercase tracking-wide">
              Override Status
            </label>
            <select
              value={overrideStatus}
              onChange={(e) => setOverrideStatus(e.target.value as IssueStatusValue)}
              className="w-full border border-neutral-300 rounded-lg px-3 py-2 text-sm text-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-400 bg-white"
            >
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="solved">Solved</option>
              <option value="exclude">Exclude</option>
            </select>
          </div>

          {/* Override Remark */}
          <div>
            <label className="text-xs font-medium text-neutral-600 mb-1.5 block uppercase tracking-wide">
              Override Remark
            </label>
            <textarea
              value={remark}
              onChange={(e) => setRemark(e.target.value)}
              placeholder='e.g. "This specific MID was resolved separately by the bank."'
              rows={3}
              className="w-full border border-neutral-300 rounded-lg px-3 py-2 text-sm text-neutral-800 focus:outline-none focus:ring-2 focus:ring-neutral-400 resize-none bg-white placeholder-neutral-400"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-neutral-100 flex items-center justify-between">
          <button
            type="button"
            onClick={onClose}
            className="text-xs text-neutral-500 hover:text-neutral-700 font-medium cursor-pointer transition-colors"
          >
            Cancel
          </button>
          <div className="flex gap-2">
            {Object.keys(currentOverrides).length > 0 && (
              <button
                type="button"
                onClick={async () => {
                  setSaving(true);
                  try {
                    await onSave({});
                    onClose();
                  } finally {
                    setSaving(false);
                  }
                }}
                disabled={saving}
                className="text-xs text-red-500 hover:text-red-700 font-medium cursor-pointer transition-colors disabled:opacity-50"
              >
                Clear All Overrides
              </button>
            )}
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || selected.size === 0}
              className="px-4 py-1.5 bg-neutral-900 text-white text-xs font-semibold rounded-lg hover:bg-neutral-800 transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {saving ? "Saving…" : "Apply Overrides"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Copy to Clipboard Icon ───────────────────────────────────────────────────
function CopyMidsButton({ mids }: { mids: string[] }) {
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleCopy = async (e: React.MouseEvent) => {
    e.stopPropagation();
    await navigator.clipboard.writeText(mids.join(", "));
    setCopied(true);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      type="button"
      onClick={handleCopy}
      title="Copy MIDs to clipboard"
      className="shrink-0 p-1 text-neutral-400 hover:text-neutral-700 transition-colors cursor-pointer rounded"
    >
      {copied ? (
        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5 text-emerald-500" viewBox="0 0 20 20" fill="currentColor">
          <path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd" />
        </svg>
      ) : (
        <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
          <path d="M8 3a1 1 0 011-1h2a1 1 0 110 2H9a1 1 0 01-1-1z" />
          <path d="M6 3a2 2 0 00-2 2v11a2 2 0 002 2h8a2 2 0 002-2V5a2 2 0 00-2-2 3 3 0 01-3 3H9a3 3 0 01-3-3z" />
        </svg>
      )}
    </button>
  );
}

// ─── Issue Table Row ──────────────────────────────────────────────────────────
function IssueTableRow({
  row,
  onUpdateIssue,
}: {
  row: FlatIssueRow;
  onUpdateIssue: (
    issueId: number,
    patch: { status?: IssueStatusValue; comment?: string; mid_overrides?: Record<string, MidOverride> }
  ) => Promise<void>;
}) {
  const [comment, setComment] = useState(row.comment ?? "");
  const [saving, setSaving] = useState(false);
  const [showOverrideModal, setShowOverrideModal] = useState(false);

  useEffect(() => {
    setComment(row.comment ?? "");
  }, [row.comment]);

  const handleStatusChange = async (newStatus: IssueStatusValue) => {
    if (!row.issueId) return;
    setSaving(true);
    try {
      await onUpdateIssue(row.issueId, { status: newStatus });
    } finally {
      setSaving(false);
    }
  };

  const handleCommentBlur = async () => {
    if (!row.issueId || comment === (row.comment ?? "")) return;
    setSaving(true);
    try {
      await onUpdateIssue(row.issueId, { comment });
    } finally {
      setSaving(false);
    }
  };

  const handleApplySuggestion = async (suggestedComment: string) => {
    if (!row.issueId) return;
    setSaving(true);
    try {
      await onUpdateIssue(row.issueId, { status: "solved", comment: suggestedComment });
      setComment(suggestedComment);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveOverrides = async (overrides: Record<string, MidOverride>) => {
    if (!row.issueId) return;
    await onUpdateIssue(row.issueId, { mid_overrides: overrides });
  };

  const overriddenMids = new Set(Object.keys(row.mid_overrides || {}));
  const hasOverrides = overriddenMids.size > 0;

  return (
    <>
      <tr className="border-b border-neutral-200 hover:bg-neutral-50/50 transition-colors">
        {/* Partner */}
        <td className="py-3 px-4 text-sm font-semibold text-neutral-800 align-top whitespace-nowrap">
          {row.partnerName}
        </td>

        {/* Category */}
        <td className="py-3 px-4 text-sm text-neutral-700 align-top font-medium">
          {row.category}
        </td>

        {/* Affected MIDs */}
        <td className="py-3 px-4 text-xs align-top max-w-xs">
          <div className="flex items-start gap-1.5">
            <div className="flex-1 font-mono text-neutral-600 bg-neutral-100/80 px-2 py-1.5 rounded border border-neutral-200 leading-relaxed max-h-24 overflow-y-auto break-all">
              {row.affectedMids.map((mid, i) => (
                <span key={mid}>
                  <span
                    className={
                      overriddenMids.has(mid)
                        ? "text-red-600 font-semibold bg-red-50 rounded px-0.5"
                        : "text-neutral-600"
                    }
                    title={
                      overriddenMids.has(mid)
                        ? `Overridden → ${row.mid_overrides?.[mid]?.status}`
                        : undefined
                    }
                  >
                    {mid}
                  </span>
                  {i < row.affectedMids.length - 1 && ", "}
                </span>
              ))}
            </div>
            <div className="flex flex-col gap-1 shrink-0 pt-0.5">
              <CopyMidsButton mids={row.affectedMids} />
              {row.issueId && row.affectedMids.length > 1 && (
                <button
                  type="button"
                  onClick={() => setShowOverrideModal(true)}
                  title="Override status for specific MIDs"
                  className={`p-1 rounded transition-colors cursor-pointer ${
                    hasOverrides
                      ? "text-red-500 hover:text-red-700"
                      : "text-neutral-400 hover:text-neutral-700"
                  }`}
                >
                  {/* Wrench/Override icon */}
                  <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M11.49 3.17c-.38-1.56-2.6-1.56-2.98 0a1.532 1.532 0 01-2.286.948c-1.372-.836-2.942.734-2.106 2.106.54.886.061 2.042-.947 2.287-1.561.379-1.561 2.6 0 2.978a1.532 1.532 0 01.947 2.287c-.836 1.372.734 2.942 2.106 2.106a1.532 1.532 0 012.287.947c.379 1.561 2.6 1.561 2.978 0a1.533 1.533 0 012.287-.947c1.372.836 2.942-.734 2.106-2.106a1.533 1.533 0 01.947-2.287c1.561-.379 1.561-2.6 0-2.978a1.532 1.532 0 01-.947-2.287c.836-1.372-.734-2.942-2.106-2.106a1.532 1.532 0 01-2.287-.947zM10 13a3 3 0 100-6 3 3 0 000 6z" clipRule="evenodd" />
                  </svg>
                </button>
              )}
            </div>
          </div>
          {hasOverrides && (
            <p className="text-[10px] text-red-500 mt-1 font-medium">
              {overriddenMids.size} MID{overriddenMids.size > 1 ? "s" : ""} overridden
            </p>
          )}
        </td>

        {/* Txns */}
        <td className="py-3 px-4 text-sm text-center align-top whitespace-nowrap font-medium text-neutral-700">
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-neutral-100 text-neutral-800">
            {row.count}
          </span>
        </td>

        {/* Status */}
        <td className="py-3 px-4 text-sm align-top whitespace-nowrap">
          <select
            value={row.status}
            disabled={saving || !row.issueId}
            onChange={(e) => handleStatusChange(e.target.value as IssueStatusValue)}
            className={`text-xs font-semibold px-2.5 py-1.5 rounded-md border shadow-sm focus:outline-none cursor-pointer transition-colors ${
              STATUS_OPTIONS.find((s) => s.value === row.status)?.style ?? ""
            }`}
          >
            {STATUS_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value} className="bg-white text-neutral-900 font-normal">
                {opt.label}
              </option>
            ))}
          </select>
        </td>

        {/* Comment */}
        <td className="py-3 px-4 text-sm align-top">
          <textarea
            value={comment}
            disabled={!row.issueId}
            onChange={(e) => setComment(e.target.value)}
            onBlur={handleCommentBlur}
            placeholder='e.g. "MID configuration fixed"'
            rows={2}
            className="w-full text-xs border border-neutral-300 rounded-md px-2.5 py-1.5 focus:ring-1 focus:ring-neutral-400 focus:outline-none resize-y min-h-[42px] bg-white text-neutral-800 placeholder-neutral-400"
          />
          {row.last_solved_comment && row.status !== "solved" && (
            <div className="text-[10px] text-amber-600 mt-1 flex items-wrap items-center gap-1 leading-normal">
              <span>💡 Suggestion: "{row.last_solved_comment}"</span>
              <button
                type="button"
                onClick={() => handleApplySuggestion(row.last_solved_comment!)}
                className="text-neutral-900 underline font-semibold hover:text-black cursor-pointer inline-block"
              >
                [Apply]
              </button>
            </div>
          )}
          {saving && <span className="text-[10px] text-neutral-400 block mt-0.5">Saving…</span>}
        </td>
      </tr>

      {/* Override Modal */}
      {showOverrideModal && (
        <MidOverrideModal
          affectedMids={row.affectedMids}
          currentOverrides={row.mid_overrides || {}}
          onSave={handleSaveOverrides}
          onClose={() => setShowOverrideModal(false)}
        />
      )}
    </>
  );
}

// ─── Issue Table ──────────────────────────────────────────────────────────────
export default function IssueTable({
  rows,
  onUpdateIssue,
  emptyMessage = "No issues found in this category.",
}: {
  rows: FlatIssueRow[];
  onUpdateIssue: (
    issueId: number,
    patch: { status?: IssueStatusValue; comment?: string; mid_overrides?: Record<string, MidOverride> }
  ) => Promise<void>;
  emptyMessage?: string;
}) {
  if (rows.length === 0) {
    return <p className="text-neutral-400 text-sm py-4">{emptyMessage}</p>;
  }

  return (
    <div className="overflow-x-auto border border-neutral-200 rounded-lg shadow-sm bg-white">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="bg-neutral-100/80 border-b border-neutral-200 text-xs font-semibold text-neutral-600 uppercase tracking-wider">
            <th className="py-3 px-4">Partner / Entity</th>
            <th className="py-3 px-4">Issue Category</th>
            <th className="py-3 px-4">Affected MIDs</th>
            <th className="py-3 px-4 text-center">Txns</th>
            <th className="py-3 px-4">Solved Status</th>
            <th className="py-3 px-4 min-w-[220px]">Ops Comment</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-200">
          {rows.map((row, idx) => (
            <IssueTableRow key={row.issueId ?? `row-${idx}`} row={row} onUpdateIssue={onUpdateIssue} />
          ))}
        </tbody>
      </table>
    </div>
  );
}
