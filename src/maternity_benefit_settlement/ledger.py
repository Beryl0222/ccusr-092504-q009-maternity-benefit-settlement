"""生育保障跨域结算账的领域规则。

在 contracts.py 的交换契约之上，本模块把经办要求的机制实现为可复算的
纯函数，不依赖第三方包：

- 跨地区请求凭稳定业务键幂等受理，两地推导结果一致；
- 费用项目按发生时有效的政策归类，零自付只覆盖合规范围，
  非政策项目给出可解释明细；
- 基础分娩与并发症两个支付范围不得重复支付同一项目；
- 资格变化或病案更正只重算下游受影响的事实；
- 已到账资金以补付、冲正、分期追回调整，原流水不被覆盖；
- 支付步骤经不可重复的检查点恢复；
- 医院、参保地、家庭各自看到必要信息，账户变更需双重核验。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence

ROLE_HOSPITAL = "hospital"
ROLE_INSURER = "insurer"
ROLE_FAMILY = "family"
ROLES = (ROLE_HOSPITAL, ROLE_INSURER, ROLE_FAMILY)

SCOPE_BASIC_DELIVERY = "basic_delivery"
SCOPE_COMPLICATION = "complication"

ADJUSTMENT_SUPPLEMENT = "supplement"
ADJUSTMENT_REVERSAL = "reversal"
ADJUSTMENT_INSTALLMENT = "installment_recovery"
ADJUSTMENT_KINDS = (ADJUSTMENT_SUPPLEMENT, ADJUSTMENT_REVERSAL, ADJUSTMENT_INSTALLMENT)


def derive_business_key(*, insured_id: str, facility_id: str, episode_no: str, benefit_kind: str) -> str:
    """跨地区稳定业务键：只依赖业务事实，不含时间戳与随机数。

    就医地与参保地按同一规则推导，任一地的重复受理都会被识别。
    """
    parts = {
        "insured_id": insured_id,
        "facility_id": facility_id,
        "episode_no": episode_no,
        "benefit_kind": benefit_kind,
    }
    for name, value in parts.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"业务键要素 {name} 必须是非空字符串")
    material = "|".join(parts[name].strip() for name in sorted(parts))
    return "MBS-" + sha256(material.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class BenefitPolicy:
    """某一统筹区在一段生效期内的生育待遇政策。"""

    policy_id: str
    region: str
    effective_from: str
    effective_to: str | None
    covered_items: frozenset[str]

    def effective_on(self, day: str) -> bool:
        if day < self.effective_from:
            return False
        return self.effective_to is None or day < self.effective_to


@dataclass(frozen=True)
class ChargeClassification:
    """费用项目按发生时政策归类的结果。"""

    charge_id: str
    policy_id: str | None
    scope: str
    covered: bool
    explanation: str | None


def classify_charge(charge: Mapping[str, Any], policies: Iterable[BenefitPolicy]) -> ChargeClassification:
    """按费用发生时有效的政策归类。

    临床归属（基础分娩或并发症）与是否合规分开记录；非政策项目必须
    留下可解释的原因，供个人端展示明细。
    """
    charge_id = str(charge.get("charge_id", "")).strip()
    if not charge_id:
        raise ValueError("费用项目缺少 charge_id")
    item_code = str(charge.get("item_code", "")).strip()
    occurred_on = str(charge.get("occurred_on", "")).strip()
    region = str(charge.get("region", "")).strip()
    scope = SCOPE_COMPLICATION if charge.get("complication_group_id") else SCOPE_BASIC_DELIVERY

    candidates = [p for p in policies if p.region == region and p.effective_on(occurred_on)]
    if not candidates:
        return ChargeClassification(
            charge_id=charge_id,
            policy_id=None,
            scope=scope,
            covered=False,
            explanation=f"费用发生日 {occurred_on} 在统筹区 {region} 无生效政策，项目 {item_code} 无法纳入待遇",
        )
    policy = sorted(candidates, key=lambda p: (p.effective_from, p.policy_id))[-1]
    if item_code in policy.covered_items:
        return ChargeClassification(charge_id, policy.policy_id, scope, True, None)
    item_name = str(charge.get("item_name", "")).strip() or item_code
    return ChargeClassification(
        charge_id=charge_id,
        policy_id=policy.policy_id,
        scope=scope,
        covered=False,
        explanation=f"项目 {item_code}（{item_name}）不在政策 {policy.policy_id} 合规范围内，需个人自付",
    )


def out_of_pocket_fen(amount_fen: int, classification: ChargeClassification) -> int:
    """零自付只覆盖合规范围；非政策项目由个人全额承担。"""
    if amount_fen < 0:
        raise ValueError("费用金额不得为负")
    return 0 if classification.covered else amount_fen


def find_scope_conflicts(classifications: Iterable[ChargeClassification]) -> list[str]:
    """同一项目在基础分娩与并发症下都被支付即为重复，返回其 charge_id。"""
    payable: dict[str, set[str]] = {}
    for item in classifications:
        if item.covered:
            payable.setdefault(item.charge_id, set()).add(item.scope)
    return sorted(cid for cid, scopes in payable.items() if len(scopes) > 1)


@dataclass(frozen=True)
class Installment:
    due_on: str
    amount_fen: int


@dataclass(frozen=True)
class Adjustment:
    """对已到账资金的调整计划，始终引用原流水而不覆盖它。"""

    adjustment_id: str
    kind: str
    original_flow_id: str
    amount_fen: int
    reason: str
    installments: tuple[Installment, ...] = ()


def plan_adjustment(
    *,
    adjustment_id: str,
    kind: str,
    original_flow_id: str,
    amount_fen: int,
    reason: str,
    installments: Sequence[Installment] = (),
) -> Adjustment:
    if not adjustment_id.strip():
        raise ValueError("调整必须有标识")
    if kind not in ADJUSTMENT_KINDS:
        raise ValueError(f"调整方式必须是 {ADJUSTMENT_KINDS} 之一")
    if not original_flow_id.strip():
        raise ValueError("调整必须引用原流水，不得覆盖")
    if amount_fen <= 0:
        raise ValueError("调整金额必须为正")
    if not reason.strip():
        raise ValueError("调整必须说明原因")
    plan = tuple(installments)
    if kind == ADJUSTMENT_INSTALLMENT:
        if not plan:
            raise ValueError("分期追回必须给出分期计划")
        if any(item.amount_fen <= 0 for item in plan):
            raise ValueError("每期金额必须为正")
        if sum(item.amount_fen for item in plan) != amount_fen:
            raise ValueError("分期金额合计必须等于追回总额")
        dues = [item.due_on for item in plan]
        if dues != sorted(dues) or len(set(dues)) != len(dues):
            raise ValueError("分期到期日必须递增且不重复")
    elif plan:
        raise ValueError("补付与冲正不分期")
    return Adjustment(adjustment_id, kind, original_flow_id, amount_fen, reason, plan)


@dataclass(frozen=True)
class CheckpointLog:
    """支付步骤的检查点记录：已完成的步骤不可重复执行。"""

    completed: tuple[int, ...] = ()

    def record(self, seq: int) -> "CheckpointLog":
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise ValueError("检查点序号必须是从 1 开始的整数")
        if seq in self.completed:
            raise ValueError(f"检查点 {seq} 已完成，不可重复执行")
        return CheckpointLog(tuple(sorted((*self.completed, seq))))

    def remaining(self, steps: Sequence[str]) -> list[str]:
        """中断恢复后仍需执行的步骤。"""
        done = set(self.completed)
        return [name for index, name in enumerate(steps, start=1) if index not in done]


_HIDDEN_FROM_HOSPITAL = frozenset({"account_number", "fund_clearing"})
_HIDDEN_FROM_FAMILY = frozenset({"fund_clearing"})
_MASKED_FOR_FAMILY = frozenset({"account_number"})


def _mask_account(value: str) -> str:
    return "*" * max(len(value) - 4, 0) + value[-4:] if value else value


def project_event(event: Mapping[str, Any], role: str) -> dict[str, Any] | None:
    """按角色投影事件：不在可见范围内返回 None，敏感字段剔除或脱敏。"""
    if role not in ROLES:
        raise ValueError(f"角色必须是 {ROLES} 之一")
    visible_to = event.get("visibility")
    if visible_to is not None and role not in visible_to:
        return None
    payload = dict(event.get("payload", {}))
    if role == ROLE_HOSPITAL:
        for field in _HIDDEN_FROM_HOSPITAL:
            payload.pop(field, None)
    elif role == ROLE_FAMILY:
        for field in _HIDDEN_FROM_FAMILY:
            payload.pop(field, None)
        for field in _MASKED_FOR_FAMILY:
            if field in payload:
                payload[field] = _mask_account(str(payload[field]))
    projected = {key: value for key, value in event.items() if key != "payload"}
    projected["payload"] = payload
    return projected


@dataclass(frozen=True)
class AccountChangeDecision:
    approved: bool
    reason: str


def decide_account_change(
    request: Mapping[str, Any], confirmations: Iterable[Mapping[str, Any]]
) -> AccountChangeDecision:
    """收款账户变更需双重核验：两名不同核验人，且都不是申请人。"""
    request_id = request.get("request_id")
    requester = request.get("requested_by")
    verifiers: list[str] = []
    for confirmation in confirmations:
        if confirmation.get("request_id") != request_id:
            continue
        verifier = confirmation.get("verifier_id")
        if not verifier or verifier == requester or verifier in verifiers:
            continue
        verifiers.append(str(verifier))
    if len(verifiers) >= 2:
        return AccountChangeDecision(True, "双重核验通过")
    return AccountChangeDecision(False, "需要两名不同的核验人确认，且核验人不能是申请人")


# 事实之间的依赖方向：上游被更正时，下游才需要重算。
DEPENDENCIES: Mapping[str, tuple[str, ...]] = {
    "benefit_policy": ("service_package", "charge_item"),
    "service_package": ("charge_item",),
    "delivery_episode": ("complication_group", "charge_item"),
    "complication_group": ("charge_item",),
    "insured_person": ("allowance_entitlement",),
    "charge_item": ("personal_payment", "fund_settlement"),
    "allowance_entitlement": ("personal_payment",),
}


def affected_fact_types(corrected_type: str) -> tuple[str, ...]:
    """资格变化或病案更正时，只重算下游受影响的事实类型。"""
    if corrected_type not in DEPENDENCIES:
        return ()
    seen: set[str] = set()
    queue = list(DEPENDENCIES[corrected_type])
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        queue.extend(DEPENDENCIES.get(current, ()))
    return tuple(sorted(seen))


@dataclass(frozen=True)
class MoneyFlow:
    """一条资金流水；原流水与调整流水都只追加、不改写。"""

    flow_id: str
    kind: str
    amount_fen: int
    references: str | None
    payer_region: str | None
    payee_region: str | None


@dataclass(frozen=True)
class LedgerState:
    """事件回放的确定性结果，经办可据此复算跨域清分与调整。"""

    accepted_keys: tuple[str, ...]
    duplicates: tuple[str, ...]
    classifications: tuple[ChargeClassification, ...]
    flows: tuple[MoneyFlow, ...]
    checkpoints: Mapping[str, tuple[int, ...]]
    issues: tuple[str, ...]


def replay(events: Iterable[Mapping[str, Any]]) -> LedgerState:
    """按来源顺序回放事件流，同一事件序列必然得到同一状态。"""
    accepted: list[str] = []
    duplicates: list[str] = []
    classifications: list[ChargeClassification] = []
    flows: list[MoneyFlow] = []
    checkpoints: dict[str, CheckpointLog] = {}
    issues: list[str] = []

    for event in events:
        event_type = event.get("event_type")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            payload = {}

        if event_type == "CASE_ACCEPTED":
            key = event.get("business_key")
            if isinstance(key, str) and key:
                if key in accepted:
                    duplicates.append(key)
                else:
                    accepted.append(key)

        elif event_type == "CHARGE_CLASSIFIED":
            classifications.append(
                ChargeClassification(
                    charge_id=str(payload.get("charge_id", "")),
                    policy_id=payload.get("policy_id"),
                    scope=str(payload.get("scope", SCOPE_BASIC_DELIVERY)),
                    covered=bool(payload.get("covered", False)),
                    explanation=payload.get("explanation"),
                )
            )

        elif event_type == "PAYMENT_POSTED":
            flows.append(
                MoneyFlow(
                    flow_id=str(payload.get("flow_id", "")),
                    kind="original",
                    amount_fen=int(payload.get("amount_fen", 0)),
                    references=None,
                    payer_region=payload.get("payer_region"),
                    payee_region=payload.get("payee_region"),
                )
            )
            seq = payload.get("checkpoint_seq")
            if isinstance(seq, int) and not isinstance(seq, bool):
                owner = str(event.get("aggregate_id", ""))
                log = checkpoints.get(owner, CheckpointLog())
                try:
                    checkpoints[owner] = log.record(seq)
                except ValueError:
                    issues.append(f"{owner} 的检查点 {seq} 重复，已按不可重复原则拦截")

        elif event_type == "ADJUSTMENT_APPLIED":
            original = str(payload.get("original_flow_id", ""))
            if original and all(flow.flow_id != original for flow in flows):
                issues.append(f"调整引用了不存在的原流水 {original}")
            flows.append(
                MoneyFlow(
                    flow_id=str(payload.get("flow_id", "")),
                    kind=str(payload.get("kind", "")),
                    amount_fen=int(payload.get("amount_fen", 0)),
                    references=original or None,
                    payer_region=payload.get("payer_region"),
                    payee_region=payload.get("payee_region"),
                )
            )

    return LedgerState(
        accepted_keys=tuple(accepted),
        duplicates=tuple(duplicates),
        classifications=tuple(classifications),
        flows=tuple(flows),
        checkpoints={owner: log.completed for owner, log in checkpoints.items()},
        issues=tuple(issues),
    )


_STAGE_BY_EVENT = {
    "CASE_ACCEPTED": "已受理",
    "CHARGE_CLASSIFIED": "费用已归类",
    "COMPLICATION_GROUPED": "并发症已入组",
    "SETTLEMENT_AUTHORIZED": "结算已核定",
    "ALLOWANCE_QUALIFIED": "津贴资格已确认",
    "PAYMENT_POSTED": "资金已拨付",
    "ADJUSTMENT_APPLIED": "待遇已调整",
    "ACCOUNT_CHANGE_CONFIRMED": "收款账户已变更",
}


def progress_view(events: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """按业务对象汇总最新处理阶段，供家庭逐项查看费用与待遇进度。"""
    latest: dict[str, dict[str, str]] = {}
    for event in events:
        stage = _STAGE_BY_EVENT.get(str(event.get("event_type")))
        if stage is None:
            continue
        payload = event.get("payload")
        subject = payload.get("subject_id") if isinstance(payload, Mapping) else None
        subject = subject or event.get("aggregate_id")
        if not subject:
            continue
        key = f"{event.get('aggregate_type')}:{subject}"
        latest[key] = {
            "subject": str(subject),
            "stage": stage,
            "at": str(event.get("occurred_at", "")),
        }
    return [latest[key] for key in sorted(latest)]


def clearing_report(state: LedgerState) -> list[dict[str, Any]]:
    """按地区对与流水类型汇总金额，供经办复算跨域清分与后续调整。"""
    totals: dict[tuple[str | None, str | None, str], int] = {}
    for flow in state.flows:
        key = (flow.payer_region, flow.payee_region, flow.kind)
        totals[key] = totals.get(key, 0) + flow.amount_fen
    return [
        {
            "payer_region": payer,
            "payee_region": payee,
            "kind": kind,
            "amount_fen": totals[(payer, payee, kind)],
        }
        for payer, payee, kind in sorted(totals, key=lambda item: tuple(str(part) for part in item))
    ]
