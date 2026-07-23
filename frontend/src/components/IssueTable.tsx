import { useState, useEffect } from "react";
import type { IssueStatusValue } from "../lib/api";

export type FlatIssueRow = {
  issueId: number | null;
  partnerName: string;
  category: string;
  count: number;
  affectedMids: string[];
  status: IssueStatusValue;
  comment: string | null;
  last_solved_comment?: string | null;
};

const STATUS_OPTIONS: { value: IssueStatusValue; label: string; style: string }[] = [
  { value: "pending", label: "Pending", style: "bg-neutral-100 text-neutral-700 border-neutral-300" },
  { value: "in_progress", label: "In Progress", style: "bg-amber-50 text-amber-800 border-amber-300" },
  { value: "solved", label: "Solved", style: "bg-emerald-50 text-emerald-800 border-emerald-300" },
  { value: "exclude", label: "Exclude", style: "bg-gray-200 text-gray-600 border-gray-300" },
];

function IssueTableRow({
  row,
  onUpdateIssue,
}: {
  row: FlatIssueRow;
  onUpdateIssue: (issueId: number, patch: { status?: IssueStatusValue; comment?: string }) => Promise<void>;
}) {
  const [comment, setComment] = useState(row.comment ?? "");
  const [saving, setSaving] = useState(false);

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

  return (
    <tr className="border-b border-neutral-200 hover:bg-neutral-50/50 transition-colors">
      <td className="py-3 px-4 text-sm font-semibold text-neutral-800 align-top whitespace-nowrap">
        {row.partnerName}
      </td>
      <td className="py-3 px-4 text-sm text-neutral-700 align-top font-medium">
        {row.category}
      </td>
      <td className="py-3 px-4 text-xs align-top max-w-xs">
        <div className="font-mono text-neutral-600 bg-neutral-100/80 px-2 py-1.5 rounded border border-neutral-200 break-all leading-tight max-h-20 overflow-y-auto">
          {row.affectedMids.join(", ")}
        </div>
      </td>
      <td className="py-3 px-4 text-sm text-center align-top whitespace-nowrap font-medium text-neutral-700">
        <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold bg-neutral-100 text-neutral-800">
          {row.count}
        </span>
      </td>
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
  );
}

export default function IssueTable({
  rows,
  onUpdateIssue,
  emptyMessage = "No issues found in this category.",
}: {
  rows: FlatIssueRow[];
  onUpdateIssue: (issueId: number, patch: { status?: IssueStatusValue; comment?: string }) => Promise<void>;
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
