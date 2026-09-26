"""费用项目按发生时政策归类；基础分娩与并发症支付不得重复。

零自付只覆盖合规范围（基本服务包内且政策为零自付）；
非政策项目必须给出可解释明细，供家庭与经办核对。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum

from .facts import (
    ChargeItem,
    ComplicationGroup,
    PolicyWindow,
    ServicePackage,
    policy_at,
)


class Category(str, Enum):
    BASIC_PACKAGE = "basic_package"            # 基本服务包内，零自付
    COMPLICATION = "complication"              # 并发症病组，参保地基金结算
    ANALGESIA_MATERIAL = "analgesia_material"  # 镇痛和药耗，按政策另行结算
    NON_POLICY = "non_policy"                  # 非政策项目，个人自付并给出明细


@dataclass(frozen=True)
class Classification:
    charge_id: str
    category: Category
    policy_id: str | None
    zero_self_pay: bool
    explanation: str


def classify_charge(
    charge: ChargeItem,
    policies: Iterable[PolicyWindow],
    packages: Mapping[str, ServicePackage],
    groups: Iterable[ComplicationGroup],
    region: str,
) -> Classification:
    """按费用发生日的生效政策归类；基本服务包优先于并发症病组。"""
    policy = policy_at(policies, region, charge.occurred_on)
    if policy is None:
        return Classification(
            charge.charge_id,
            Category.NON_POLICY,
            None,
            False,
            f"费用发生日 {charge.occurred_on.isoformat()} 在 {region} 无生效政策",
        )

    package = packages.get(policy.package_id)
    if package is not None and package.covers(charge.item_code):
        return Classification(
            charge.charge_id,
            Category.BASIC_PACKAGE,
            policy.policy_id,
            policy.zero_self_pay,
            f"基本服务包 {package.package_id} 内项目",
        )

    for group in groups:
        if group.episode_id == charge.episode_id and charge.item_code in group.item_codes:
            return Classification(
                charge.charge_id,
                Category.COMPLICATION,
                policy.policy_id,
                False,
                f"并发症病组 {group.group_code}",
            )

    if charge.bill_kind in ("analgesia", "material"):
        return Classification(
            charge.charge_id,
            Category.ANALGESIA_MATERIAL,
            policy.policy_id,
            False,
            "镇痛和药耗，按政策另行结算",
        )

    return Classification(
        charge.charge_id,
        Category.NON_POLICY,
        policy.policy_id,
        False,
        f"项目 {charge.item_code} 不在基本服务包 {policy.package_id} 目录，按非政策项目自付",
    )


@dataclass(frozen=True)
class PayableLine:
    charge_id: str
    scope: str  # 支付范围：basic_package / complication / analgesia_material
    amount_fen: int


def resolve_payable(
    charges: Iterable[ChargeItem],
    classifications: Mapping[str, Classification],
    groups: Iterable[ComplicationGroup],
    packages: Mapping[str, ServicePackage],
) -> tuple[list[PayableLine], list[str]]:
    """汇总各支付范围的应付明细。

    同一费用项目只进入一个支付范围：基本服务包优先；与病组重叠的项目
    在说明中列明，并发症范围不再重复支付。非政策项目不进支付范围。
    """
    package_items: set[str] = set()
    for package in packages.values():
        package_items |= package.item_codes

    lines: list[PayableLine] = []
    notes: list[str] = []
    for charge in charges:
        classification = classifications[charge.charge_id]
        if classification.category is Category.BASIC_PACKAGE:
            overlapping = [
                group.group_code
                for group in groups
                if group.episode_id == charge.episode_id and charge.item_code in group.item_codes
            ]
            if overlapping:
                notes.append(
                    f"项目 {charge.item_code} 同属病组 {','.join(overlapping)}，"
                    "已在基本服务包支付，并发症范围不再重复支付"
                )
            lines.append(PayableLine(charge.charge_id, Category.BASIC_PACKAGE.value, charge.amount_fen))
        elif classification.category is Category.COMPLICATION:
            if charge.item_code in package_items:
                notes.append(
                    f"项目 {charge.item_code} 已在基本服务包目录内，并发症范围不再重复支付"
                )
                continue
            lines.append(PayableLine(charge.charge_id, Category.COMPLICATION.value, charge.amount_fen))
        elif classification.category is Category.ANALGESIA_MATERIAL:
            lines.append(PayableLine(charge.charge_id, Category.ANALGESIA_MATERIAL.value, charge.amount_fen))
    return lines, notes
