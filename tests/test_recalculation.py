from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.classification import PayableLine
from maternity_benefit_settlement.facts import ChargeItem
from maternity_benefit_settlement.ledger import EntryKind, Ledger, LedgerEntry, settlement_key
from maternity_benefit_settlement.recalculation import (
    Correction,
    affected_charges,
    apply_scope_deltas,
    scope_deltas,
)
from datetime import date

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 20, 9, 0, tzinfo=CN)


def charge(charge_id: str, episode: str = "E-1") -> ChargeItem:
    return ChargeItem(charge_id, episode, "X", "项目", 100, date(2026, 8, 2), "other")


class AffectedChargeTests(unittest.TestCase):
    def test_only_listed_charges_of_the_episode(self) -> None:
        charges = [charge("C-1"), charge("C-2"), charge("C-3", episode="E-2")]
        correction = Correction("COR-1", "E-1", frozenset({"C-2"}), "病案更正")
        self.assertEqual(["C-2"], [c.charge_id for c in affected_charges(charges, correction)])

    def test_none_means_whole_episode(self) -> None:
        charges = [charge("C-1"), charge("C-2"), charge("C-3", episode="E-2")]
        correction = Correction("COR-1", "E-1", None, "资格变化")
        self.assertEqual(["C-1", "C-2"], [c.charge_id for c in affected_charges(charges, correction)])


class ScopeDeltaTests(unittest.TestCase):
    def test_deltas_are_grouped_by_scope_and_zero_is_dropped(self) -> None:
        old = [PayableLine("C-1", "complication", 100), PayableLine("C-2", "complication", 200)]
        new = [PayableLine("C-1", "complication", 100), PayableLine("C-3", "analgesia_material", 50)]
        self.assertEqual({"complication": -200, "analgesia_material": 50}, scope_deltas(old, new))


class ApplyDeltaTests(unittest.TestCase):
    def test_deltas_post_supplement_or_reversal_only_on_touched_scopes(self) -> None:
        ledger = Ledger()
        ledger.post(
            LedgerEntry("S-1", settlement_key("E-1", "complication"), EntryKind.FUND_SETTLEMENT, 30000, NOW, "结算")
        )
        ledger.post(
            LedgerEntry("S-2", settlement_key("E-1", "analgesia_material"), EntryKind.FUND_SETTLEMENT, 5000, NOW, "结算")
        )
        correction = Correction("COR-1", "E-1", frozenset({"C-2"}), "病案更正")
        posted = apply_scope_deltas(ledger, correction, {"complication": -20000}, NOW)

        self.assertEqual([EntryKind.REVERSAL], [e.kind for e in posted])
        self.assertEqual(10000, ledger.remaining("S-1"))
        # 未受影响的范围保持原样
        self.assertEqual(5000, ledger.remaining("S-2"))
        self.assertEqual(1, len(ledger.history("S-2")))

        # 以同一更正单号重试是幂等的
        again = apply_scope_deltas(ledger, correction, {"complication": -20000}, NOW)
        self.assertEqual([e.entry_id for e in posted], [e.entry_id for e in again])
        self.assertEqual(3, len(ledger.entries()))


if __name__ == "__main__":
    unittest.main()
