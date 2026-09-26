"""跨域结算账的十类独立但可对账的事实。

所有金额以“分”为单位（整数），避免浮点误差进入对账。
每类事实都有稳定业务键：两地系统对同一业务事实必须得到同一个键，
才能防止跨地区重复受理。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


def business_key(*parts: object) -> str:
    """跨地区受理共用的稳定业务键。

    片段去空白后以冒号连接；片段为空或含冒号都会让两地拼出不同的键，
    因此直接拒绝。
    """
    segments = [str(part).strip() for part in parts]
    if any(not segment for segment in segments):
        raise ValueError("业务键片段不得为空")
    if any(":" in segment for segment in segments):
        raise ValueError("业务键片段不得包含分隔符 ':'")
    return ":".join(segments)


@dataclass(frozen=True)
class InsuredRelation:
    """参保关系：谁在哪个统筹区以何种身份参保。"""

    person_id: str
    region: str
    employment_type: str
    valid_from: date
    valid_to: date | None = None

    @property
    def key(self) -> str:
        return business_key("insured", self.person_id, self.region)

    def covers(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day <= self.valid_to)


@dataclass(frozen=True)
class PolicyWindow:
    """政策地区与生效期。"""

    policy_id: str
    region: str
    package_id: str
    zero_self_pay: bool
    effective_from: date
    effective_to: date | None = None

    @property
    def key(self) -> str:
        return business_key("policy", self.policy_id)

    def effective_on(self, day: date) -> bool:
        return self.effective_from <= day and (self.effective_to is None or day <= self.effective_to)


def policy_at(policies: Iterable[PolicyWindow], region: str, day: date) -> PolicyWindow | None:
    """发生时政策：按费用发生日定位生效政策；重叠时取生效日最新者。"""
    candidates = [p for p in policies if p.region == region and p.effective_on(day)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.effective_from)


@dataclass(frozen=True)
class DeliveryEpisode:
    """分娩住院。"""

    episode_id: str
    person_id: str
    hospital_id: str
    hospital_region: str
    admitted_on: date
    discharged_on: date

    @property
    def key(self) -> str:
        return business_key("episode", self.episode_id)


@dataclass(frozen=True)
class ServicePackage:
    """基本服务包：零自付的合规范围。"""

    package_id: str
    item_codes: frozenset[str]

    @property
    def key(self) -> str:
        return business_key("package", self.package_id)

    def covers(self, item_code: str) -> bool:
        return item_code in self.item_codes


@dataclass(frozen=True)
class ChargeItem:
    """费用项目（含镇痛和药耗），按发生时政策归类。"""

    charge_id: str
    episode_id: str
    item_code: str
    name: str
    amount_fen: int
    occurred_on: date
    bill_kind: str  # 医院收费类别：basic / analgesia / material / complication / other

    @property
    def key(self) -> str:
        return business_key("charge", self.charge_id)


@dataclass(frozen=True)
class ComplicationGroup:
    """并发症病组。"""

    group_code: str
    episode_id: str
    item_codes: frozenset[str]
    diagnosed_on: date

    @property
    def key(self) -> str:
        return business_key("complication", self.episode_id, self.group_code)


@dataclass(frozen=True)
class PersonalPayment:
    """个人支付。"""

    payment_id: str
    episode_id: str
    amount_fen: int
    paid_on: date

    @property
    def key(self) -> str:
        return business_key("personal_payment", self.payment_id)


@dataclass(frozen=True)
class FundSettlement:
    """基金结算。"""

    settlement_id: str
    episode_id: str
    payer_region: str
    amount_fen: int
    settled_on: date
    batch_id: str

    @property
    def key(self) -> str:
        return business_key("fund_settlement", self.settlement_id)


@dataclass(frozen=True)
class AllowanceEligibility:
    """津贴资格：生育津贴、育儿补贴可分批次处理。"""

    allowance_id: str
    person_id: str
    allowance_type: str
    amount_fen: int
    qualified: bool
    decided_on: date
    batch_id: str

    @property
    def key(self) -> str:
        return business_key("allowance", self.allowance_id)
