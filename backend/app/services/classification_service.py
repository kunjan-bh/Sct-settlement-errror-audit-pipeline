"""
Classification service.

This is your pasted function, restructured so the rules come from the
database (editable via UI/API) instead of being hardcoded lists. The
matching LOGIC is identical -- same three match types, same "first match
wins in priority order" behavior -- only the SOURCE of the rules changed.

Why a class instead of a bare function: rules don't change mid-batch, but
they DO change between batches (that's the whole point of making them
editable). Loading all active rules from the DB once per classification
run, instead of once per row, is the difference between ~2 queries and
~20,000 queries on a large settlement file. `RuleEngine.load()` does that
one-time load; `classify_row()` just does in-memory matching after that.
"""
from dataclasses import dataclass
from typing import Optional

from app.models.classification_rule import ClassificationRule
from app.models.partner_mapping import PartnerMapping


@dataclass
class ClassificationResult:
    side: str  # "sct" | "aggregator" | "bank" | "unknown"
    category: str
    matched_rule_id: Optional[int]


class RuleEngine:
    """Loads active rules once, then classifies many rows against them."""

    def __init__(self, rules: list[ClassificationRule]):
        # Rules are pre-sorted by priority so classify_row can just walk
        # the list in order and stop at the first match -- mirrors the
        # `break` in your original for-loops.
        self._rules = sorted(rules, key=lambda r: r.priority)

    @classmethod
    def load(cls) -> "RuleEngine":
        rules = ClassificationRule.query.filter_by(active=True).all()
        return cls(rules)

    def classify_row(self, remark: str) -> ClassificationResult:
        remark_lower = (remark or "").strip().lower()

        for rule in self._rules:
            if self._matches(rule, remark_lower):
                return ClassificationResult(
                    side=rule.side, category=rule.category, matched_rule_id=rule.id
                )

        return ClassificationResult(side="unknown", category="Unclassified", matched_rule_id=None)

    @staticmethod
    def _matches(rule: ClassificationRule, remark_lower: str) -> bool:
        pattern = rule.pattern.lower()
        if rule.match_type == "contains":
            return pattern in remark_lower
        if rule.match_type == "starts_with":
            return remark_lower.startswith(pattern)
        if rule.match_type == "exact":
            return remark_lower == pattern
        return False


class PartnerResolver:
    """
    Resolves a MID to (partner_name, bucket) via exact member-code lookup.
    Member code = first 3 characters of the MID (your rule: always 3 chars,
    zero-padded on the left if the source code was only 2).

    Built as a dict (member_code -> mapping) rather than a scanned list --
    O(1) per row instead of O(n_mappings) per row, which matters once
    you've got hundreds of aggregator-member rows imported.

    Unmapped MIDs resolve to ("No Aggregator", None), matching your
    original spec's fallback behavior.
    """

    def __init__(self, mappings: list[PartnerMapping]):
        self._by_code: dict[str, PartnerMapping] = {m.member_code: m for m in mappings}

    @classmethod
    def load(cls) -> "PartnerResolver":
        mappings = PartnerMapping.query.filter_by(active=True).all()
        return cls(mappings)

    def resolve(self, mid: str) -> tuple[str, Optional[str]]:
        mid = (mid or "").strip()
        member_code = mid[:3]

        mapping = self._by_code.get(member_code)
        if mapping is None:
            return "No Aggregator", None

        return mapping.partner_name, mapping.bucket
