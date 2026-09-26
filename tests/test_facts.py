from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.facts import (
    InsuredRelation,
    PolicyWindow,
    business_key,
    policy_at,
)


class BusinessKeyTests(unittest.TestCase):
    def test_key_is_stable_and_stripped(self) -> None:
        self.assertEqual("insured:P-1:杭州", business_key("insured", " P-1 ", "杭州"))

    def test_blank_segment_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            business_key("insured", "  ")

    def test_separator_inside_segment_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            business_key("insured", "P-1:杭州")


class FactWindowTests(unittest.TestCase):
    def test_insured_relation_covers_boundaries(self) -> None:
        relation = InsuredRelation("P-1", "杭州", "灵活就业", date(2026, 1, 1), date(2026, 12, 31))
        self.assertTrue(relation.covers(date(2026, 1, 1)))
        self.assertTrue(relation.covers(date(2026, 12, 31)))
        self.assertFalse(relation.covers(date(2027, 1, 1)))
        self.assertEqual("insured:P-1:杭州", relation.key)

    def test_open_window_covers_any_later_day(self) -> None:
        relation = InsuredRelation("P-1", "杭州", "灵活就业", date(2026, 1, 1))
        self.assertTrue(relation.covers(date(2030, 5, 1)))

    def test_policy_at_picks_latest_effective_on_overlap(self) -> None:
        old = PolicyWindow("POL-1", "上海", "PKG-1", True, date(2026, 1, 1))
        new = PolicyWindow("POL-2", "上海", "PKG-2", False, date(2026, 7, 1))
        self.assertEqual("POL-2", policy_at([old, new], "上海", date(2026, 8, 1)).policy_id)
        self.assertEqual("POL-1", policy_at([old, new], "上海", date(2026, 3, 1)).policy_id)

    def test_policy_at_returns_none_without_match(self) -> None:
        policy = PolicyWindow("POL-1", "上海", "PKG-1", True, date(2026, 1, 1), date(2026, 6, 30))
        self.assertIsNone(policy_at([policy], "上海", date(2026, 7, 1)))
        self.assertIsNone(policy_at([policy], "杭州", date(2026, 3, 1)))


if __name__ == "__main__":
    unittest.main()
