"""只增不改的结算流水账。

已到账资金的调整（补付、冲正、分期追回）一律以新流水入账并引用原流水，
原流水永不覆盖。入账按稳定业务键幂等：同一键同一内容视为重试直接返回
原流水；同一键不同内容说明两地受理出了冲突，必须拒绝并暴露。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .facts import business_key


class EntryKind(str, Enum):
    FUND_SETTLEMENT = "fund_settlement"  # 基金结算
    ALLOWANCE = "allowance"              # 津贴发放
    SUPPLEMENT = "supplement"            # 补付
    REVERSAL = "reversal"                # 冲正
    RECOVERY = "recovery"                # 分期追回


_ADJUSTMENT_KINDS = frozenset({EntryKind.SUPPLEMENT, EntryKind.REVERSAL, EntryKind.RECOVERY})


class LedgerError(ValueError):
    pass


@dataclass(frozen=True)
class LedgerEntry:
    entry_id: str
    business_key: str
    kind: EntryKind
    amount_fen: int  # 正数=支付，负数=冲正/追回
    posted_at: datetime
    reason: str
    reference_id: str | None = None  # 调整流水指向的原流水
    batch_id: str | None = None


def settlement_key(episode_id: str, scope: str) -> str:
    """参保地基金结算流水的稳定业务键（按住院与支付范围）。"""
    return business_key("fund", episode_id, scope)


def _require_tz(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise LedgerError("入账时间必须包含时区")


class Ledger:
    """只增不改：没有任何修改或删除既有流水的入口。"""

    def __init__(self) -> None:
        self._entries: list[LedgerEntry] = []
        self._by_key: dict[str, LedgerEntry] = {}
        self._by_id: dict[str, LedgerEntry] = {}

    def post(self, entry: LedgerEntry) -> LedgerEntry:
        """按稳定业务键幂等入账，防止两地重复受理。"""
        _require_tz(entry.posted_at)
        existing = self._by_key.get(entry.business_key)
        if existing is not None:
            if (existing.kind, existing.amount_fen, existing.reference_id) != (
                entry.kind,
                entry.amount_fen,
                entry.reference_id,
            ):
                raise LedgerError("同一业务键提交了不同内容，疑似两地重复受理冲突")
            return existing
        if entry.entry_id in self._by_id:
            raise LedgerError(f"流水号 {entry.entry_id} 已存在")
        if entry.kind in _ADJUSTMENT_KINDS:
            if entry.reference_id is None or entry.reference_id not in self._by_id:
                raise LedgerError("调整流水必须引用已存在的原流水")
        self._entries.append(entry)
        self._by_key[entry.business_key] = entry
        self._by_id[entry.entry_id] = entry
        return entry

    def by_key(self, key: str) -> LedgerEntry | None:
        return self._by_key.get(key)

    def by_id(self, entry_id: str) -> LedgerEntry | None:
        return self._by_id.get(entry_id)

    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    def history(self, entry_id: str) -> tuple[LedgerEntry, ...]:
        """一笔流水及其全部调整，按入账顺序。"""
        root = self._require(entry_id)
        return tuple([root] + [e for e in self._entries if e.reference_id == entry_id])

    def history_by_key(self, key: str) -> tuple[LedgerEntry, ...]:
        root = self._by_key.get(key)
        return () if root is None else self.history(root.entry_id)

    def remaining(self, entry_id: str) -> int:
        """某笔流水连同其调整的净额。"""
        return sum(e.amount_fen for e in self.history(entry_id))

    def balance(self) -> int:
        return sum(e.amount_fen for e in self._entries)

    def supplement(
        self,
        original_id: str,
        entry_id: str,
        amount_fen: int,
        posted_at: datetime,
        reason: str,
        batch_id: str | None = None,
    ) -> LedgerEntry:
        """补付：少付的部分以新流水补足。"""
        self._require(original_id)
        if amount_fen <= 0:
            raise LedgerError("补付金额必须为正")
        return self.post(
            LedgerEntry(
                entry_id=entry_id,
                business_key=business_key("supplement", entry_id),
                kind=EntryKind.SUPPLEMENT,
                amount_fen=amount_fen,
                posted_at=posted_at,
                reason=reason,
                reference_id=original_id,
                batch_id=batch_id,
            )
        )

    def reverse(
        self,
        original_id: str,
        entry_id: str,
        amount_fen: int,
        posted_at: datetime,
        reason: str,
    ) -> LedgerEntry:
        """冲正：按负数以新流水入账，不得超过原流水剩余净额。"""
        self._require(original_id)
        if amount_fen <= 0:
            raise LedgerError("冲正金额必须为正")
        entry = LedgerEntry(
            entry_id=entry_id,
            business_key=business_key("reversal", entry_id),
            kind=EntryKind.REVERSAL,
            amount_fen=-amount_fen,
            posted_at=posted_at,
            reason=reason,
            reference_id=original_id,
        )
        # 幂等重试直接交给 post 做冲突检测；额度校验只针对新请求
        if self._by_key.get(entry.business_key) is None and amount_fen > self.remaining(original_id):
            raise LedgerError("冲正金额超过原流水剩余净额")
        return self.post(entry)

    def recover_in_installments(
        self,
        original_id: str,
        request_id: str,
        total_fen: int,
        installments: int,
        posted_at: datetime,
        reason: str,
    ) -> list[LedgerEntry]:
        """分期追回：生成一组追回流水，避免对家庭的整笔追回。"""
        self._require(original_id)
        if installments < 1:
            raise LedgerError("分期期数必须为正")
        if total_fen <= 0:
            raise LedgerError("追回金额必须为正")
        # 幂等重试由 post 做冲突检测；额度校验只针对新请求
        is_retry = self._by_key.get(business_key("recovery", request_id, "1")) is not None
        if not is_retry and total_fen > self.remaining(original_id):
            raise LedgerError("追回金额超过原流水剩余净额")
        base, extra = divmod(total_fen, installments)
        entries = []
        for index in range(installments):
            amount = base + (1 if index < extra else 0)
            entries.append(
                self.post(
                    LedgerEntry(
                        entry_id=f"{request_id}-{index + 1}",
                        business_key=business_key("recovery", request_id, str(index + 1)),
                        kind=EntryKind.RECOVERY,
                        amount_fen=-amount,
                        posted_at=posted_at,
                        reason=reason,
                        reference_id=original_id,
                    )
                )
            )
        return entries

    def _require(self, entry_id: str) -> LedgerEntry:
        entry = self._by_id.get(entry_id)
        if entry is None:
            raise LedgerError(f"原流水 {entry_id} 不存在")
        return entry
