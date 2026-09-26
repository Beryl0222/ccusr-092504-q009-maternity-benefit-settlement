from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.classification import (
    Category,
    classify_charge,
    resolve_payable,
)
from maternity_benefit_settlement.facts import (
    ChargeItem,
    ComplicationGroup,
    PolicyWindow,
    ServicePackage,
)

POLICY = PolicyWindow("POL-SH", "上海", "PKG-SH", True, date(2026, 1, 1))
PACKAGES = {"PKG-SH": ServicePackage("PKG-SH", frozenset({"A001", "D1"}))}
GROUPS = [ComplicationGroup("G-PPH", "E-1", frozenset({"K301", "D1"}), date(2026, 8, 3))]


def charge(charge_id: str, item_code: str, kind: str, day: date = date(2026, 8, 2)) -> ChargeItem:
    return ChargeItem(charge_id, "E-1", item_code, f"项目{item_code}", 10000, day, kind)


def classify(c: ChargeItem):
    return classify_charge(c, [POLICY], PACKAGES, GROUPS, "上海")


class ClassifyTests(unittest.TestCase):
    def test_package_item_is_zero_self_pay(self) -> None:
        result = classify(charge("C-1", "A001", "basic"))
        self.assertEqual(Category.BASIC_PACKAGE, result.category)
        self.assertTrue(result.zero_self_pay)
        self.assertEqual("POL-SH", result.policy_id)

    def test_complication_item_is_fund_settled_not_zero_self_pay(self) -> None:
        result = classify(charge("C-2", "K301", "complication"))
        self.assertEqual(Category.COMPLICATION, result.category)
        self.assertFalse(result.zero_self_pay)
        self.assertIn("G-PPH", result.explanation)

    def test_analgesia_and_material_have_own_category(self) -> None:
        for kind in ("analgesia", "material"):
            result = classify(charge("C-3", "M101", kind))
            self.assertEqual(Category.ANALGESIA_MATERIAL, result.category)
            self.assertFalse(result.zero_self_pay)

    def test_non_policy_item_gets_explainable_detail(self) -> None:
        result = classify(charge("C-4", "X900", "other"))
        self.assertEqual(Category.NON_POLICY, result.category)
        self.assertFalse(result.zero_self_pay)
        self.assertIn("X900", result.explanation)
        self.assertIn("PKG-SH", result.explanation)

    def test_charge_outside_policy_window_is_non_policy(self) -> None:
        result = classify(charge("C-5", "A001", "basic", day=date(2025, 12, 31)))
        self.assertEqual(Category.NON_POLICY, result.category)
        self.assertIsNone(result.policy_id)
        self.assertIn("无生效政策", result.explanation)


class ResolvePayableTests(unittest.TestCase):
    def test_item_in_both_package_and_group_is_paid_once(self) -> None:
        charges = [charge("C-1", "D1", "basic"), charge("C-2", "K301", "complication")]
        classifications = {c.charge_id: classify(c) for c in charges}
        lines, notes = resolve_payable(charges, classifications, GROUPS, PACKAGES)
        self.assertEqual(["basic_package", "complication"], [line.scope for line in lines])
        self.assertEqual(1, len(notes))
        self.assertIn("不再重复支付", notes[0])

    def test_non_policy_item_is_not_payable(self) -> None:
        charges = [charge("C-1", "X900", "other")]
        classifications = {c.charge_id: classify(c) for c in charges}
        lines, notes = resolve_payable(charges, classifications, GROUPS, PACKAGES)
        self.assertEqual([], lines)
        self.assertEqual([], notes)


if __name__ == "__main__":
    unittest.main()
