"""分角色视图与清分复算。

家庭看到每项费用和待遇的处理进度；医院只看到本院结算所需信息；
参保地看到完整清分。经办可从事实出发复算跨域清分，并与流水核对。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .classification import Category, Classification, PayableLine
from .facts import AllowanceEligibility, ChargeItem, DeliveryEpisode, business_key
from .ledger import EntryKind, Ledger, LedgerEntry, settlement_key


@dataclass(frozen=True)
class ItemProgress:
    """家庭视角的一项费用或待遇的进度。"""

    subject: str
    stage: str
    amount_fen: int
    detail: str


def _chain_stage(chain: tuple[LedgerEntry, ...]) -> str:
    if not chain:
        return "待结算"
    kinds = {entry.kind for entry in chain}
    net = sum(entry.amount_fen for entry in chain)
    if net == 0:
        return "已冲正"
    if EntryKind.RECOVERY in kinds:
        return "分期追回中"
    if EntryKind.REVERSAL in kinds:
        return "已部分冲正"
    if EntryKind.SUPPLEMENT in kinds:
        return "已补付"
    return "已结算"


def family_progress(
    charges: Iterable[ChargeItem],
    classifications: Mapping[str, Classification],
    ledger: Ledger,
    episode_id: str,
    allowances: Iterable[AllowanceEligibility],
) -> tuple[ItemProgress, ...]:
    """家庭看到每项费用和待遇的处理进度，非政策项目附可解释明细。"""
    items: list[ItemProgress] = []
    for charge in charges:
        classification = classifications[charge.charge_id]
        if classification.category is Category.NON_POLICY:
            items.append(ItemProgress(charge.name, "个人自付", charge.amount_fen, classification.explanation))
        elif classification.zero_self_pay:
            items.append(ItemProgress(charge.name, "零自付（基本服务包）", 0, classification.explanation))
        else:
            chain = ledger.history_by_key(settlement_key(episode_id, classification.category.value))
            items.append(
                ItemProgress(charge.name, _chain_stage(chain), charge.amount_fen, classification.explanation)
            )
    for allowance in allowances:
        chain = ledger.history_by_key(business_key("allowance", allowance.allowance_id))
        items.append(
            ItemProgress(
                allowance.allowance_type,
                _chain_stage(chain),
                allowance.amount_fen,
                f"批次 {allowance.batch_id}",
            )
        )
    return tuple(items)


@dataclass(frozen=True)
class HospitalView:
    """医院视角：本院住院的结算情况，不含个人收款账户与津贴明细。"""

    episode_id: str
    hospital_id: str
    zero_self_pay_fen: int
    fund_settled_fen: int
    lines: tuple[str, ...]


def hospital_view(
    episode: DeliveryEpisode,
    charges: Iterable[ChargeItem],
    classifications: Mapping[str, Classification],
    ledger: Ledger,
) -> HospitalView:
    zero_self_pay = 0
    lines: list[str] = []
    for charge in charges:
        classification = classifications[charge.charge_id]
        if classification.zero_self_pay:
            zero_self_pay += charge.amount_fen
        lines.append(f"{charge.name}（{charge.item_code}）：{classification.explanation}")
    prefix = f"fund:{episode.episode_id}:"
    fund_settled = sum(
        entry.amount_fen for entry in ledger.entries() if entry.business_key.startswith(prefix)
    )
    return HospitalView(
        episode_id=episode.episode_id,
        hospital_id=episode.hospital_id,
        zero_self_pay_fen=zero_self_pay,
        fund_settled_fen=fund_settled,
        lines=tuple(lines),
    )


@dataclass(frozen=True)
class InsurerView:
    """参保地视角：完整应付明细、去重说明与各范围流水链。"""

    episode_id: str
    person_id: str
    payable: tuple[PayableLine, ...]
    notes: tuple[str, ...]
    chains: tuple[tuple[str, tuple[LedgerEntry, ...]], ...]
    allowances: tuple[AllowanceEligibility, ...]


def insurer_view(
    episode: DeliveryEpisode,
    payable: Iterable[PayableLine],
    notes: Iterable[str],
    ledger: Ledger,
    allowances: Iterable[AllowanceEligibility],
) -> InsurerView:
    payable = tuple(payable)
    scopes = sorted({line.scope for line in payable})
    chains = tuple(
        (scope, ledger.history_by_key(settlement_key(episode.episode_id, scope))) for scope in scopes
    )
    return InsurerView(
        episode_id=episode.episode_id,
        person_id=episode.person_id,
        payable=payable,
        notes=tuple(notes),
        chains=chains,
        allowances=tuple(allowances),
    )


@dataclass(frozen=True)
class ClearingStatement:
    """经办复算结果：各范围应付、零自付合计与个人自付合计。"""

    episode_id: str
    totals_by_scope: tuple[tuple[str, int], ...]
    zero_self_pay_fen: int
    non_policy_fen: int


def recompute_clearing(
    episode_id: str,
    charges: Iterable[ChargeItem],
    classifications: Mapping[str, Classification],
    payable_lines: Iterable[PayableLine],
) -> ClearingStatement:
    """从事实出发复算跨域清分；零自付部分在医院端结算，不进参保地流水。"""
    zero_self_pay = 0
    non_policy = 0
    for charge in charges:
        classification = classifications[charge.charge_id]
        if classification.category is Category.NON_POLICY:
            non_policy += charge.amount_fen
        elif classification.zero_self_pay:
            zero_self_pay += charge.amount_fen
    totals: dict[str, int] = {}
    for line in payable_lines:
        if classifications[line.charge_id].zero_self_pay:
            continue
        totals[line.scope] = totals.get(line.scope, 0) + line.amount_fen
    return ClearingStatement(
        episode_id=episode_id,
        totals_by_scope=tuple(sorted(totals.items())),
        zero_self_pay_fen=zero_self_pay,
        non_policy_fen=non_policy,
    )


def verify_clearing(statement: ClearingStatement, ledger: Ledger) -> list[str]:
    """复算结果与流水净额核对，返回差异说明（空列表=一致）。"""
    issues: list[str] = []
    for scope, expected in statement.totals_by_scope:
        chain = ledger.history_by_key(settlement_key(statement.episode_id, scope))
        posted = sum(entry.amount_fen for entry in chain)
        if posted != expected:
            issues.append(f"范围 {scope} 复算 {expected} 分与流水净额 {posted} 分不一致")
    return issues
