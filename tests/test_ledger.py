from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.ledger import (
    EntryKind,
    Ledger,
    LedgerEntry,
    LedgerError,
    settlement_key,
)

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 10, 10, 0, tzinfo=CN)


def entry(entry_id: str, key: str, amount: int, kind: EntryKind = EntryKind.FUND_SETTLEMENT) -> LedgerEntry:
    return LedgerEntry(entry_id, key, kind, amount, NOW, "测试", batch_id="B-1")


class PostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = Ledger()

    def test_post_requires_timezone(self) -> None:
        naive = LedgerEntry("E-1", "k1", EntryKind.ALLOWANCE, 100, datetime(2026, 9, 10, 10, 0), "测试")
        with self.assertRaises(LedgerError):
            self.ledger.post(naive)

    def test_same_key_same_content_is_idempotent_retry(self) -> None:
        first = self.ledger.post(entry("E-1", "k1", 100))
        second = self.ledger.post(entry("E-2", "k1", 100))
        self.assertIs(first, second)
        self.assertEqual(1, len(self.ledger.entries()))

    def test_same_key_different_content_is_conflict(self) -> None:
        self.ledger.post(entry("E-1", "k1", 100))
        with self.assertRaises(LedgerError):
            self.ledger.post(entry("E-2", "k1", 200))

    def test_adjustment_must_reference_existing_entry(self) -> None:
        orphan = LedgerEntry("A-1", "k-adj", EntryKind.REVERSAL, -50, NOW, "测试", reference_id="E-x")
        with self.assertRaises(LedgerError):
            self.ledger.post(orphan)


class AdjustmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = Ledger()
        self.ledger.post(entry("E-1", settlement_key("E-9001", "complication"), 100000))

    def test_supplement_adds_new_entry_and_keeps_original(self) -> None:
        self.ledger.supplement("E-1", "COR-1-complication", 20000, NOW, "补付")
        history = self.ledger.history("E-1")
        self.assertEqual([EntryKind.FUND_SETTLEMENT, EntryKind.SUPPLEMENT], [e.kind for e in history])
        self.assertEqual(100000, history[0].amount_fen)
        self.assertEqual(120000, self.ledger.remaining("E-1"))

    def test_reverse_is_capped_by_remaining(self) -> None:
        self.ledger.reverse("E-1", "COR-1-complication", 30000, NOW, "部分冲正")
        self.assertEqual(70000, self.ledger.remaining("E-1"))
        with self.assertRaises(LedgerError):
            self.ledger.reverse("E-1", "COR-2-complication", 70001, NOW, "超额冲正")

    def test_adjustments_are_idempotent_by_entry_id(self) -> None:
        first = self.ledger.reverse("E-1", "COR-1-complication", 30000, NOW, "部分冲正")
        second = self.ledger.reverse("E-1", "COR-1-complication", 30000, NOW, "部分冲正")
        self.assertIs(first, second)
        self.assertEqual(2, len(self.ledger.entries()))

    def test_installment_recovery_splits_amount_and_keeps_total(self) -> None:
        entries = self.ledger.recover_in_installments("E-1", "REQ-1", 100000, 3, NOW, "分期追回")
        self.assertEqual([-33334, -33333, -33333], [e.amount_fen for e in entries])
        self.assertTrue(all(e.kind is EntryKind.RECOVERY for e in entries))
        self.assertTrue(all(e.reference_id == "E-1" for e in entries))
        self.assertEqual(0, self.ledger.remaining("E-1"))

    def test_recovery_cannot_exceed_remaining(self) -> None:
        with self.assertRaises(LedgerError):
            self.ledger.recover_in_installments("E-1", "REQ-1", 100001, 2, NOW, "超额追回")

    def test_recovery_retry_returns_existing_entries(self) -> None:
        first = self.ledger.recover_in_installments("E-1", "REQ-1", 90000, 3, NOW, "分期追回")
        second = self.ledger.recover_in_installments("E-1", "REQ-1", 90000, 3, NOW, "分期追回")
        self.assertEqual([e.entry_id for e in first], [e.entry_id for e in second])
        self.assertEqual(4, len(self.ledger.entries()))


if __name__ == "__main__":
    unittest.main()
