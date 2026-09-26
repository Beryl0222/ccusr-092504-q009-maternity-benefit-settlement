"""收款账户与双重核验变更。

账户是待遇发放的落点，变更必须经两名不同核验人、两个不同渠道确认，
任一条件不满足都拒绝变更。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from .facts import business_key


@dataclass(frozen=True)
class ReceivingAccount:
    """收款账户。"""

    account_id: str
    person_id: str
    bank: str
    account_no_masked: str

    @property
    def key(self) -> str:
        return business_key("account", self.account_id)


@dataclass(frozen=True)
class AccountChangeRequest:
    request_id: str
    person_id: str
    new_account: ReceivingAccount
    requested_at: datetime


@dataclass(frozen=True)
class Confirmation:
    verifier_id: str
    channel: str  # 核验渠道，如“经办复核”“银行要素校验”
    confirmed_at: datetime


class AccountChangeError(PermissionError):
    pass


def apply_account_change(
    request: AccountChangeRequest, confirmations: Iterable[Confirmation]
) -> ReceivingAccount:
    """双重核验：两名不同核验人、两个不同渠道，缺一不可。"""
    confirmations = list(confirmations)
    verifiers = {c.verifier_id for c in confirmations}
    channels = {c.channel for c in confirmations}
    if len(verifiers) < 2 or len(channels) < 2:
        raise AccountChangeError("收款账户变更需双重核验：不同核验人、不同渠道各确认一次")
    if request.new_account.person_id != request.person_id:
        raise AccountChangeError("新账户与参保人不一致")
    return request.new_account
