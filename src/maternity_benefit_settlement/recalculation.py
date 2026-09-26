"""资格变化或病案更正只重算受影响部分。

更正只圈定受影响的费用项目；新旧应付按支付范围汇总差额，
正差补付、负差冲正，全部以调整流水落到受影响范围的原结算上，
未受影响的流水保持不动。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from .classification import PayableLine
from .facts import ChargeItem
from .ledger import Ledger, LedgerEntry, LedgerError, settlement_key


@dataclass(frozen=True)
class Correction:
    """病案更正或资格变化：声明受影响范围。charge_ids 为 None 表示该次住院全部费用。"""

    correction_id: str
    episode_id: str
    charge_ids: frozenset[str] | None
    reason: str


def affected_charges(charges: Iterable[ChargeItem], correction: Correction) -> list[ChargeItem]:
    """只挑出本次更正影响的费用项目。"""
    return [
        charge
        for charge in charges
        if charge.episode_id == correction.episode_id
        and (correction.charge_ids is None or charge.charge_id in correction.charge_ids)
    ]


def scope_deltas(
    old_lines: Iterable[PayableLine], new_lines: Iterable[PayableLine]
) -> dict[str, int]:
    """按支付范围汇总差额：正=需补付，负=需冲正；无差额的范围不出现。"""
    deltas: dict[str, int] = {}
    for line in old_lines:
        deltas[line.scope] = deltas.get(line.scope, 0) - line.amount_fen
    for line in new_lines:
        deltas[line.scope] = deltas.get(line.scope, 0) + line.amount_fen
    return {scope: delta for scope, delta in deltas.items() if delta != 0}


def apply_scope_deltas(
    ledger: Ledger,
    correction: Correction,
    deltas: Mapping[str, int],
    posted_at: datetime,
) -> list[LedgerEntry]:
    """把差额落到受影响范围的结算流水上；以更正单号幂等，可安全重试。"""
    posted: list[LedgerEntry] = []
    for scope in sorted(deltas):
        root = ledger.by_key(settlement_key(correction.episode_id, scope))
        if root is None:
            raise LedgerError(f"范围 {scope} 尚未结算，无法调整")
        entry_id = f"{correction.correction_id}-{scope}"
        delta = deltas[scope]
        if delta > 0:
            posted.append(ledger.supplement(root.entry_id, entry_id, delta, posted_at, correction.reason))
        else:
            posted.append(ledger.reverse(root.entry_id, entry_id, -delta, posted_at, correction.reason))
    return posted
