import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { FiChevronDown } from "react-icons/fi";
import type { PartnerSummary, IssueStatusValue } from "../lib/api";
import IssueCard from "./IssueCard";

export default function PartnerCard({
  partner,
  onUpdateIssue,
}: {
  partner: PartnerSummary;
  onUpdateIssue: (issueId: number, patch: { status?: IssueStatusValue; comment?: string }) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="border border-neutral-200 rounded-xl bg-white shadow-sm overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-4 text-left hover:bg-neutral-50 transition-colors"
      >
        <div>
          <p className="font-medium text-neutral-900">{partner.partner_name}</p>
          <p className="text-sm text-neutral-400 mt-0.5">
            Failed: {partner.failed} &nbsp;·&nbsp; Pending: {partner.pending} &nbsp;·&nbsp; Issues: {partner.issue_count}
          </p>
        </div>
        <motion.span animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }}>
          <FiChevronDown className="text-neutral-400" />
        </motion.span>
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
            <div className="p-4 space-y-2 bg-neutral-50">
              {partner.issues.map((issue) => (
                <IssueCard
                  key={`${partner.partner_name}-${issue.category}`}
                  issue={issue}
                  onUpdate={(patch) => (issue.id != null ? onUpdateIssue(issue.id, patch) : Promise.resolve())}
                />
              ))}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
