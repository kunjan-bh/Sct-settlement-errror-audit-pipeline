import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { FiChevronDown } from "react-icons/fi";
import type { Issue, IssueStatusValue } from "../lib/api";

const STATUS_STYLES: Record<IssueStatusValue, string> = {
  pending: "bg-neutral-100 text-neutral-600",
  in_progress: "bg-amber-50 text-amber-700",
  solved: "bg-green-50 text-green-700",
  exclude: "bg-gray-200 text-gray-600",
  lo_progress: "bg-blue-50 text-blue-700",
  success: "bg-emerald-50 text-emerald-700",
};

const STATUS_LABELS: Record<IssueStatusValue, string> = {
  pending: "Pending",
  in_progress: "In Progress",
  solved: "Solved",
  exclude: "Exclude",
  lo_progress: "Lo Progress",
  success: "Success",
};

export default function IssueCard({
  issue,
  onUpdate,
}: {
  issue: Issue;
  onUpdate: (patch: { status?: IssueStatusValue; comment?: string; mid_overrides?: Record<string, IssueStatusValue> }) => Promise<void>;
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

  const handleMidOverrideChange = async (mid: string, overrideStatus: string) => {
    const currentOverrides = issue.mid_overrides || {};
    const newOverrides = { ...currentOverrides };
    
    if (overrideStatus === "") {
      delete newOverrides[mid];
    } else {
      const numAffected = issue.affected_mids.length;
      if (numAffected > 1) {
        const potentialCount = Object.keys(newOverrides).filter(k => k !== mid).length + 1;
        if (potentialCount >= numAffected) {
          alert("You cannot override all MIDs in a group. Please change the group status instead.");
          return;
        }
      }
      newOverrides[mid] = overrideStatus as IssueStatusValue;
    }

    setSaving(true);
    try {
      await onUpdate({ mid_overrides: newOverrides });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border border-neutral-200 rounded-lg overflow-visible">
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
            className="overflow-visible border-t border-neutral-100 relative z-10"
          >
            <div className="px-4 py-3 space-y-3">
              <div>
                <p className="text-xs text-neutral-400 mb-2">Affected MIDs ({issue.affected_mids.length}) - Select to override status</p>
                <div className="flex flex-wrap gap-2">
                  {issue.affected_mids.map(mid => {
                    const override = issue.mid_overrides?.[mid];
                    const isOverridden = !!override;
                    return (
                      <div key={mid} className="inline-flex items-center bg-white border border-neutral-200 rounded text-xs">
                        <span className={`px-2 py-1 font-mono ${isOverridden ? 'text-red-600 font-medium' : 'text-neutral-600'}`}>
                          {mid}
                        </span>
                        <select
                          value={override || ""}
                          disabled={saving}
                          onChange={(e) => handleMidOverrideChange(mid, e.target.value)}
                          className="bg-transparent text-neutral-500 border-l border-neutral-200 py-1 px-1 focus:outline-none appearance-none cursor-pointer"
                          style={{ WebkitAppearance: 'none', MozAppearance: 'none' }}
                          title="Override Status"
                        >
                          <option value="">Group Status</option>
                          <option value="pending">Pending</option>
                          <option value="in_progress">In Progress</option>
                          <option value="solved">Solved</option>
                          <option value="exclude">Exclude</option>
                          <option value="lo_progress">Lo Progress</option>
                          <option value="success">Success</option>
                        </select>
                      </div>
                    );
                  })}
                </div>
              </div>

              <div className="flex items-center gap-2">
                <label className="text-xs text-neutral-400">Solved Status</label>
                <select
                  value={issue.status}
                  disabled={saving}
                  onChange={(e) => handleStatusChange(e.target.value as IssueStatusValue)}
                  className="border border-neutral-300 rounded-md px-2 py-1 text-sm bg-white text-neutral-800"
                >
                  <option value="pending">Pending</option>
                  <option value="in_progress">In Progress</option>
                  <option value="solved">Solved</option>
                  <option value="exclude">Exclude</option>
                  <option value="lo_progress">Lo Progress</option>
                  <option value="success">Success</option>
                </select>
              </div>

              <div>
                <label className="text-xs text-neutral-400 block mb-1">Comment</label>
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  onBlur={handleCommentBlur}
                  placeholder='e.g. "Aggregator confirmed configuration fixed."'
                  className="w-full border border-neutral-300 rounded-md px-3 py-2 text-sm h-16 resize-none bg-white text-neutral-800"
                />
                {issue.last_solved_comment && issue.status !== "solved" && (
                  <div className="text-[10px] text-amber-600 mt-1 flex items-wrap items-center gap-1 leading-normal">
                    <span>💡 Suggestion: "{issue.last_solved_comment}"</span>
                    <button
                      type="button"
                      onClick={async () => {
                        setSaving(true);
                        try {
                          await onUpdate({ status: "solved", comment: issue.last_solved_comment! });
                          setComment(issue.last_solved_comment!);
                        } finally {
                          setSaving(false);
                        }
                      }}
                      className="text-neutral-900 underline font-semibold hover:text-black cursor-pointer inline-block"
                    >
                      [Apply]
                    </button>
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
