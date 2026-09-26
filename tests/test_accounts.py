from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.accounts import (
    AccountChangeError,
    AccountChangeRequest,
    Confirmation,
    ReceivingAccount,
    apply_account_change,
)

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 10, 10, 0, tzinfo=CN)

NEW_ACCOUNT = ReceivingAccount("AC-2", "P-1", "建设银行", "****5678")
REQUEST = AccountChangeRequest("REQ-1", "P-1", NEW_ACCOUNT, NOW)


def confirmation(verifier: str, channel: str) -> Confirmation:
    return Confirmation(verifier, channel, NOW)


class AccountChangeTests(unittest.TestCase):
    def test_single_confirmation_is_rejected(self) -> None:
        with self.assertRaises(AccountChangeError):
            apply_account_change(REQUEST, [confirmation("经办甲", "经办复核")])

    def test_same_verifier_twice_is_rejected(self) -> None:
        confirmations = [confirmation("经办甲", "经办复核"), confirmation("经办甲", "银行要素校验")]
        with self.assertRaises(AccountChangeError):
            apply_account_change(REQUEST, confirmations)

    def test_same_channel_twice_is_rejected(self) -> None:
        confirmations = [confirmation("经办甲", "经办复核"), confirmation("经办乙", "经办复核")]
        with self.assertRaises(AccountChangeError):
            apply_account_change(REQUEST, confirmations)

    def test_two_verifiers_two_channels_pass(self) -> None:
        confirmations = [confirmation("经办甲", "经办复核"), confirmation("经办乙", "银行要素校验")]
        self.assertEqual(NEW_ACCOUNT, apply_account_change(REQUEST, confirmations))

    def test_account_of_another_person_is_rejected(self) -> None:
        foreign = AccountChangeRequest(
            "REQ-2", "P-1", ReceivingAccount("AC-3", "P-2", "建设银行", "****9999"), NOW
        )
        confirmations = [confirmation("经办甲", "经办复核"), confirmation("经办乙", "银行要素校验")]
        with self.assertRaises(AccountChangeError):
            apply_account_change(foreign, confirmations)


if __name__ == "__main__":
    unittest.main()
