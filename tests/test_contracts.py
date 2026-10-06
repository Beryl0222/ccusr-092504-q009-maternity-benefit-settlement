from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.contracts import validate_event


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads((ROOT / "contracts" / "domain.schema.json").read_text(encoding="utf-8"))
        cls.sample = json.loads((ROOT / "data" / "sample.json").read_text(encoding="utf-8"))
        cls.flow = json.loads((ROOT / "data" / "sample_flow.json").read_text(encoding="utf-8"))

    def test_sample_is_valid(self) -> None:
        self.assertEqual([], validate_event(self.sample, self.schema))

    def test_sample_flow_events_are_all_valid(self) -> None:
        for event in self.flow:
            self.assertEqual([], validate_event(event, self.schema), event["event_id"])

    def test_missing_fields_are_reported_in_stable_order(self) -> None:
        issues = validate_event({}, self.schema)
        self.assertEqual(sorted(issue.field for issue in issues), [issue.field for issue in issues])
        self.assertIn("event_id", {issue.field for issue in issues})

    def test_naive_time_and_zero_version_are_rejected(self) -> None:
        payload = dict(self.sample, occurred_at="2026-09-24T12:00:00", version=0)
        codes = {(issue.field, issue.code) for issue in validate_event(payload, self.schema)}
        self.assertIn(("occurred_at", "timezone_required"), codes)
        self.assertIn(("version", "positive_integer"), codes)

    def test_unknown_event_type_is_rejected(self) -> None:
        payload = dict(self.sample, event_type="UNKNOWN")
        issues = validate_event(payload, self.schema)
        self.assertEqual([("event_type", "unsupported_value")], [(item.field, item.code) for item in issues])

    def test_event_must_land_on_registered_aggregate(self) -> None:
        payload = dict(self.sample, event_type="POLICY_PUBLISHED", aggregate_type="insured_person")
        codes = {(issue.field, issue.code) for issue in validate_event(payload, self.schema)}
        self.assertIn(("aggregate_type", "unregistered_pair"), codes)

    def test_cross_region_acceptance_requires_business_key(self) -> None:
        payload = dict(self.sample, event_type="CASE_ACCEPTED", aggregate_type="settlement_case")
        codes = {(issue.field, issue.code) for issue in validate_event(payload, self.schema)}
        self.assertIn(("business_key", "required"), codes)

    def test_blank_business_key_is_rejected(self) -> None:
        payload = dict(self.sample, business_key="  ")
        codes = {(issue.field, issue.code) for issue in validate_event(payload, self.schema)}
        self.assertIn(("business_key", "non_empty_string"), codes)

    def test_visibility_roles_must_be_registered_and_unique(self) -> None:
        payload = dict(self.sample, visibility=["insurer", "insurer", "broker"])
        codes = {(issue.field, issue.code) for issue in validate_event(payload, self.schema)}
        self.assertIn(("visibility", "unique_items"), codes)
        self.assertIn(("visibility", "unsupported_value"), codes)

    def test_payload_must_be_object(self) -> None:
        payload = dict(self.sample, payload=["not", "an", "object"])
        codes = {(issue.field, issue.code) for issue in validate_event(payload, self.schema)}
        self.assertIn(("payload", "object_required"), codes)


if __name__ == "__main__":
    unittest.main()
