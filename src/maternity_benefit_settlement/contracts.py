"""领域事件交换契约的基础校验。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class ContractIssue:
    field: str
    code: str
    message: str


def _has_timezone(value: str) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def validate_event(payload: Any, schema: Mapping[str, Any]) -> list[ContractIssue]:
    """返回稳定排序的问题列表，不在交换层改写输入。"""
    if not isinstance(payload, Mapping):
        return [ContractIssue("$", "object_required", "事件必须是 JSON 对象")]

    issues: list[ContractIssue] = []
    required = schema.get("required", [])
    for field in required:
        if field not in payload:
            issues.append(ContractIssue(str(field), "required", "缺少必填字段"))

    for field in ("event_id", "aggregate_type", "aggregate_id", "event_type"):
        if field in payload and (not isinstance(payload[field], str) or not payload[field].strip()):
            issues.append(ContractIssue(field, "non_empty_string", "字段必须是非空字符串"))

    version = payload.get("version")
    if "version" in payload and (isinstance(version, bool) or not isinstance(version, int) or version < 1):
        issues.append(ContractIssue("version", "positive_integer", "版本必须是正整数"))

    occurred_at = payload.get("occurred_at")
    if "occurred_at" in payload and (not isinstance(occurred_at, str) or not _has_timezone(occurred_at)):
        issues.append(ContractIssue("occurred_at", "timezone_required", "发生时间必须包含时区"))

    properties = schema.get("properties", {})
    for field in ("event_type", "aggregate_type"):
        allowed = properties.get(field, {}).get("enum", [])
        value = payload.get(field)
        if isinstance(value, str) and allowed and value not in allowed:
            issues.append(ContractIssue(field, "unsupported_value", "字段值未在契约中登记"))

    issues.extend(_check_registration(payload, schema))
    issues.extend(_check_business_key(payload, schema))
    issues.extend(_check_visibility(payload, schema))

    if "payload" in payload and not isinstance(payload["payload"], Mapping):
        issues.append(ContractIssue("payload", "object_required", "载荷必须是 JSON 对象"))

    return sorted(issues, key=lambda issue: (issue.field, issue.code))


def _check_registration(payload: Mapping[str, Any], schema: Mapping[str, Any]) -> list[ContractIssue]:
    """事件类型只能落在契约登记表中列明的事实上。"""
    registration = schema.get("x-event-aggregates", {})
    event_type = payload.get("event_type")
    aggregate_type = payload.get("aggregate_type")
    if not isinstance(event_type, str) or not isinstance(aggregate_type, str):
        return []
    allowed = registration.get(event_type)
    if allowed and aggregate_type not in allowed:
        return [ContractIssue("aggregate_type", "unregistered_pair", "事件类型未登记在该类事实上")]
    return []


def _check_business_key(payload: Mapping[str, Any], schema: Mapping[str, Any]) -> list[ContractIssue]:
    """跨地区请求必须携带稳定业务键，供两地幂等受理。"""
    required_for = schema.get("x-requires-business-key", [])
    business_key = payload.get("business_key")
    present = isinstance(business_key, str) and bool(business_key.strip())
    if "business_key" in payload and not present:
        return [ContractIssue("business_key", "non_empty_string", "字段必须是非空字符串")]
    if payload.get("event_type") in required_for and not present:
        return [ContractIssue("business_key", "required", "跨地区请求必须携带稳定业务键")]
    return []


def _check_visibility(payload: Mapping[str, Any], schema: Mapping[str, Any]) -> list[ContractIssue]:
    if "visibility" not in payload:
        return []
    visibility = payload["visibility"]
    if not isinstance(visibility, list) or not visibility:
        return [ContractIssue("visibility", "non_empty_array", "可见范围必须是非空数组")]
    issues: list[ContractIssue] = []
    if len(set(map(str, visibility))) != len(visibility):
        issues.append(ContractIssue("visibility", "unique_items", "可见范围内的角色不得重复"))
    known = schema.get("properties", {}).get("visibility", {}).get("items", {}).get("enum", [])
    if any(not isinstance(role, str) or role not in known for role in visibility):
        issues.append(ContractIssue("visibility", "unsupported_value", "角色未在契约中登记"))
    return issues
