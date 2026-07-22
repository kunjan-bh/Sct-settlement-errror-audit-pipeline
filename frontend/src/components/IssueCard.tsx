import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { FiChevronDown } from "react-icons/fi";
import type { Issue, IssueStatusValue } from "../lib/api";

const STATUS_STYLES: Record<IssueStatusValue, string> = {
  pending: "bg-neutral-100 text-neutral-600",
  in_progress: "bg-amber-50 text-amber-700",
  solved: "bg-green-50 text-green-700",
};

const STATUS_LABELS: Record<IssueStatusValue, string> = {
  pending: "Pending",
  in_progress: "In Progress",
  solved: "Solved",
};

export default function IssueCard({
  issue,
  onUpdate,
}: {
  issue: Issue;
  onUpdate: (patch: { status?: IssueStatusValue; comment?: string }) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [comment, setComment] = useState(issue.comment ?? "");
  const [saving, setSaving] = useState(false);

  const handleStatusChange = async (status: IssueStatusValue) => {
    setSaving(true);
    try {
      await onUpdate({ status });
    } finally {
      setSaving(false);
    }
  };

  const handleCommentBlur = async () => {
    if (comment === (issue.comment ?? "")) return;
    setSaving(true);
    try {
      await onUpdate({ comment });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-neutral-200 rounded-lg overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-neutral-50 transition-colors"
      >
        <div className="flex items-center gap-3 min-w-0">
          <span className={`text-xs font-medium px-2 py-0.5 rounded-full shrink-0 ${STATUS_STYLES[issue.status]}`}>
            {STATUS_LABELS[issue.status]}
          </span>
          <span className="text-sm font-medium text-neutral-800 truncate">{issue.category}</span>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-sm text-neutral-400">{issue.count} txns</span>
          <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }}>
            <FiChevronDown className="text-neutral-400" />
          </motion.span>
        </div>
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-neutral-100"
          >
            <div className="px-4 py-3 space-y-3">
              <div>
                <p className="text-xs text-neutral-400 mb-1">Affected MIDs ({issue.affected_mids.length}{issue.count > issue.affected_mids.length ? "+" : ""})</p>
                <p className="text-xs font-mono text-neutral-600 break-all leading-relaxed">
                  {issue.affected_mids.join(", ")}
                </p>
              </div>

              <div className="flex items-center gap-2">
                <label className="text-xs text-neutral-400">Status</label>
                <select
                  value={issue.status}
                  disabled={saving}
                  onChange={(e) => handleStatusChange(e.target.value as IssueStatusValue)}
                  className="border border-neutral-300 rounded-md px-2 py-1 text-sm"
                >
                  <option value="pending">Pending</option>
                  <option value="in_progress">In Progress</option>
                  <option value="solved">Solved</option>
                </select>
              </div>

              <div>
                <label className="text-xs text-neutral-400 block mb-1">Comment</label>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  onBlur={handleCommentBlur}
                  placeholder='e.g. "Aggregator confirmed configuration fixed."'
                  className="w-full border border-neutral-300 rounded-md px-3 py-2 text-sm h-16 resize-none"
                />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
