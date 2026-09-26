"""联调样例文件加载：把一份 JSON 事实包解析为各类领域事实。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .accounts import ReceivingAccount
from .facts import (
    AllowanceEligibility,
    ChargeItem,
    ComplicationGroup,
    DeliveryEpisode,
    InsuredRelation,
    PolicyWindow,
    ServicePackage,
)


@dataclass(frozen=True)
class CaseFacts:
    """一次跨域结算所需的全部事实。"""

    insured_relation: InsuredRelation
    policies: tuple[PolicyWindow, ...]
    episode: DeliveryEpisode
    packages: tuple[ServicePackage, ...]
    charges: tuple[ChargeItem, ...]
    complication_groups: tuple[ComplicationGroup, ...]
    allowances: tuple[AllowanceEligibility, ...]
    receiving_account: ReceivingAccount | None


def _day(value: str | None) -> date | None:
    return None if value is None else date.fromisoformat(value)


def load_case(path: str | Path) -> CaseFacts:
    raw: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))

    relation = raw["insured_relation"]
    account = raw.get("receiving_account")
    return CaseFacts(
        insured_relation=InsuredRelation(
            person_id=relation["person_id"],
            region=relation["region"],
            employment_type=relation["employment_type"],
            valid_from=date.fromisoformat(relation["valid_from"]),
            valid_to=_day(relation.get("valid_to")),
        ),
        policies=tuple(
            PolicyWindow(
                policy_id=p["policy_id"],
                region=p["region"],
                package_id=p["package_id"],
                zero_self_pay=bool(p["zero_self_pay"]),
                effective_from=date.fromisoformat(p["effective_from"]),
                effective_to=_day(p.get("effective_to")),
            )
            for p in raw["policies"]
        ),
        episode=DeliveryEpisode(
            episode_id=raw["episode"]["episode_id"],
            person_id=raw["episode"]["person_id"],
            hospital_id=raw["episode"]["hospital_id"],
            hospital_region=raw["episode"]["hospital_region"],
            admitted_on=date.fromisoformat(raw["episode"]["admitted_on"]),
            discharged_on=date.fromisoformat(raw["episode"]["discharged_on"]),
        ),
        packages=tuple(
            ServicePackage(package_id=p["package_id"], item_codes=frozenset(p["item_codes"]))
            for p in raw["packages"]
        ),
        charges=tuple(
            ChargeItem(
                charge_id=c["charge_id"],
                episode_id=c["episode_id"],
                item_code=c["item_code"],
                name=c["name"],
                amount_fen=int(c["amount_fen"]),
                occurred_on=date.fromisoformat(c["occurred_on"]),
                bill_kind=c["bill_kind"],
            )
            for c in raw["charges"]
        ),
        complication_groups=tuple(
            ComplicationGroup(
                group_code=g["group_code"],
                episode_id=g["episode_id"],
                item_codes=frozenset(g["item_codes"]),
                diagnosed_on=date.fromisoformat(g["diagnosed_on"]),
            )
            for g in raw["complication_groups"]
        ),
        allowances=tuple(
            AllowanceEligibility(
                allowance_id=a["allowance_id"],
                person_id=a["person_id"],
                allowance_type=a["allowance_type"],
                amount_fen=int(a["amount_fen"]),
                qualified=bool(a["qualified"]),
                decided_on=date.fromisoformat(a["decided_on"]),
                batch_id=a["batch_id"],
            )
            for a in raw["allowances"]
        ),
        receiving_account=None
        if account is None
        else ReceivingAccount(
            account_id=account["account_id"],
            person_id=account["person_id"],
            bank=account["bank"],
            account_no_masked=account["account_no_masked"],
        ),
    )
