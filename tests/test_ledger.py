from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.ledger import (
    ADJUSTMENT_INSTALLMENT,
    ADJUSTMENT_REVERSAL,
    ADJUSTMENT_SUPPLEMENT,
    SCOPE_BASIC_DELIVERY,
    SCOPE_COMPLICATION,
    BenefitPolicy,
    ChargeClassification,
    CheckpointLog,
    Installment,
    affected_fact_types,
    classify_charge,
    clearing_report,
    decide_account_change,
    derive_business_key,
    find_scope_conflicts,
    out_of_pocket_fen,
    plan_adjustment,
    progress_view,
    project_event,
    replay,
)


def make_policies() -> list[BenefitPolicy]:
    return [
        BenefitPolicy("POL-A", "320505", "2026-01-01", "2026-07-01", frozenset({"F001"})),
        BenefitPolicy("POL-B", "320505", "2026-07-01", None, frozenset({"F001", "F002"})),
    ]


def make_charge(**overrides: object) -> dict:
    charge = {
        "charge_id": "CHG-1",
        "item_code": "F001",
        "item_name": "阴道分娩基础服务",
        "occurred_on": "2026-09-01",
        "region": "320505",
    }
    charge.update(overrides)
    return charge


class BusinessKeyTests(unittest.TestCase):
    def test_same_facts_derive_same_key_across_regions(self) -> None:
        facts = {
            "insured_id": "SI-330106-884213",
            "facility_id": "HOS-320505-017",
            "episode_no": "ZY-2026-09-88145",
            "benefit_kind": "delivery",
        }
        first = derive_business_key(**facts)
        second = derive_business_key(**facts)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("MBS-"))

    def test_different_episode_derives_different_key(self) -> None:
        base = {
            "insured_id": "SI-330106-884213",
            "facility_id": "HOS-320505-017",
            "benefit_kind": "delivery",
        }
        self.assertNotEqual(
            derive_business_key(episode_no="ZY-1", **base),
            derive_business_key(episode_no="ZY-2", **base),
        )

    def test_blank_fact_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            derive_business_key(
                insured_id=" ",
                facility_id="HOS-320505-017",
                episode_no="ZY-1",
                benefit_kind="delivery",
            )


class ClassificationTests(unittest.TestCase):
    def test_charge_is_classified_by_policy_effective_at_occurrence(self) -> None:
        before = classify_charge(make_charge(item_code="F002", occurred_on="2026-03-01"), make_policies())
        after = classify_charge(make_charge(item_code="F002", occurred_on="2026-09-01"), make_policies())
        self.assertEqual("POL-A", before.policy_id)
        self.assertFalse(before.covered)
        self.assertEqual("POL-B", after.policy_id)
        self.assertTrue(after.covered)

    def test_policy_window_boundary_is_exclusive(self) -> None:
        result = classify_charge(make_charge(item_code="F002", occurred_on="2026-07-01"), make_policies())
        self.assertEqual("POL-B", result.policy_id)
        self.assertTrue(result.covered)

    def test_zero_copay_only_covers_compliant_items(self) -> None:
        covered = classify_charge(make_charge(), make_policies())
        uncovered = classify_charge(make_charge(item_code="M999", item_name="进口止血材料"), make_policies())
        self.assertEqual(0, out_of_pocket_fen(45000, covered))
        self.assertEqual(45000, out_of_pocket_fen(45000, uncovered))

    def test_non_policy_item_carries_explainable_detail(self) -> None:
        result = classify_charge(
            make_charge(item_code="M999", item_name="进口止血材料"), make_policies()
        )
        self.assertFalse(result.covered)
        self.assertIsNotNone(result.explanation)
        self.assertIn("M999", result.explanation)
        self.assertIn("POL-B", result.explanation)

    def test_no_effective_policy_is_explained(self) -> None:
        result = classify_charge(make_charge(region="999999"), make_policies())
        self.assertIsNone(result.policy_id)
        self.assertFalse(result.covered)
        self.assertIn("999999", result.explanation)

    def test_complication_charge_uses_complication_scope(self) -> None:
        result = classify_charge(
            make_charge(item_code="F310", complication_group_id="CG-1"), make_policies()
        )
        self.assertEqual(SCOPE_COMPLICATION, result.scope)
        result_basic = classify_charge(make_charge(), make_policies())
        self.assertEqual(SCOPE_BASIC_DELIVERY, result_basic.scope)


class ScopeConflictTests(unittest.TestCase):
    def test_same_charge_paid_under_both_scopes_is_duplicate(self) -> None:
        classifications = [
            ChargeClassification("CHG-1", "POL-B", SCOPE_BASIC_DELIVERY, True, None),
            ChargeClassification("CHG-1", "POL-B", SCOPE_COMPLICATION, True, None),
            ChargeClassification("CHG-2", "POL-B", SCOPE_BASIC_DELIVERY, True, None),
        ]
        self.assertEqual(["CHG-1"], find_scope_conflicts(classifications))

    def test_uncovered_reclassification_is_not_double_payment(self) -> None:
        classifications = [
            ChargeClassification("CHG-1", "POL-B", SCOPE_BASIC_DELIVERY, True, None),
            ChargeClassification("CHG-1", "POL-B", SCOPE_COMPLICATION, False, "不在合规范围"),
        ]
        self.assertEqual([], find_scope_conflicts(classifications))


class AdjustmentTests(unittest.TestCase):
    def test_supplement_references_original_flow(self) -> None:
        adjustment = plan_adjustment(
            adjustment_id="ADJ-1",
            kind=ADJUSTMENT_SUPPLEMENT,
            original_flow_id="FL-1",
            amount_fen=12000,
            reason="待遇少付，补付差额",
        )
        self.assertEqual("FL-1", adjustment.original_flow_id)
        self.assertEqual((), adjustment.installments)

    def test_adjustment_must_not_stand_without_original(self) -> None:
        with self.assertRaises(ValueError):
            plan_adjustment(
                adjustment_id="ADJ-1",
                kind=ADJUSTMENT_REVERSAL,
                original_flow_id=" ",
                amount_fen=12000,
                reason="冲正",
            )

    def test_nonpositive_amount_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_adjustment(
                adjustment_id="ADJ-1",
                kind=ADJUSTMENT_REVERSAL,
                original_flow_id="FL-1",
                amount_fen=0,
                reason="冲正",
            )

    def test_installment_recovery_requires_matching_schedule(self) -> None:
        with self.assertRaises(ValueError):
            plan_adjustment(
                adjustment_id="ADJ-1",
                kind=ADJUSTMENT_INSTALLMENT,
                original_flow_id="FL-1",
                amount_fen=120000,
                reason="分期追回",
                installments=[Installment("2026-11-15", 60000)],
            )

    def test_installment_recovery_with_valid_schedule(self) -> None:
        adjustment = plan_adjustment(
            adjustment_id="ADJ-1",
            kind=ADJUSTMENT_INSTALLMENT,
            original_flow_id="FL-1",
            amount_fen=120000,
            reason="津贴重复发放，分两期追回",
            installments=[
                Installment("2026-11-15", 60000),
                Installment("2026-12-15", 60000),
            ],
        )
        self.assertEqual(2, len(adjustment.installments))

    def test_supplement_and_reversal_do_not_install(self) -> None:
        with self.assertRaises(ValueError):
            plan_adjustment(
                adjustment_id="ADJ-1",
                kind=ADJUSTMENT_SUPPLEMENT,
                original_flow_id="FL-1",
                amount_fen=12000,
                reason="补付",
                installments=[Installment("2026-11-15", 12000)],
            )


class CheckpointTests(unittest.TestCase):
    def test_completed_checkpoint_cannot_repeat(self) -> None:
        log = CheckpointLog().record(1).record(2)
        with self.assertRaises(ValueError):
            log.record(2)

    def test_resume_skips_completed_steps(self) -> None:
        steps = ["制单", "复核", "拨付", "到账确认"]
        log = CheckpointLog().record(1).record(2)
        self.assertEqual(["拨付", "到账确认"], log.remaining(steps))


class VisibilityTests(unittest.TestCase):
    def make_event(self) -> dict:
        return {
            "event_id": "EVT-1",
            "event_type": "PAYMENT_POSTED",
            "aggregate_type": "allowance_entitlement",
            "aggregate_id": "ALW-1",
            "occurred_at": "2026-10-02T10:30:00+08:00",
            "version": 2,
            "summary": "津贴拨付",
            "payload": {
                "amount_fen": 520000,
                "account_number": "6228480402564890018",
                "fund_clearing": {"share": 0.7},
            },
        }

    def test_hospital_sees_neither_account_nor_clearing(self) -> None:
        projected = project_event(self.make_event(), "hospital")
        self.assertNotIn("account_number", projected["payload"])
        self.assertNotIn("fund_clearing", projected["payload"])
        self.assertEqual(520000, projected["payload"]["amount_fen"])

    def test_family_sees_masked_account(self) -> None:
        projected = project_event(self.make_event(), "family")
        self.assertEqual("***************0018", projected["payload"]["account_number"])
        self.assertNotIn("fund_clearing", projected["payload"])

    def test_insurer_sees_full_payload(self) -> None:
        projected = project_event(self.make_event(), "insurer")
        self.assertEqual("6228480402564890018", projected["payload"]["account_number"])
        self.assertIn("fund_clearing", projected["payload"])

    def test_visibility_list_gates_role(self) -> None:
        event = dict(self.make_event(), visibility=["insurer"])
        self.assertIsNone(project_event(event, "family"))
        self.assertIsNotNone(project_event(event, "insurer"))

    def test_unknown_role_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            project_event(self.make_event(), "broker")


class AccountChangeTests(unittest.TestCase):
    def make_request(self) -> dict:
        return {"request_id": "ACR-1", "requested_by": "OPS-3301"}

    def confirm(self, verifier: str) -> dict:
        return {"request_id": "ACR-1", "verifier_id": verifier}

    def test_two_distinct_verifiers_approve(self) -> None:
        decision = decide_account_change(self.make_request(), [self.confirm("OPS-1"), self.confirm("OPS-2")])
        self.assertTrue(decision.approved)

    def test_single_verifier_is_not_enough(self) -> None:
        decision = decide_account_change(self.make_request(), [self.confirm("OPS-1")])
        self.assertFalse(decision.approved)

    def test_same_verifier_twice_is_not_dual(self) -> None:
        decision = decide_account_change(self.make_request(), [self.confirm("OPS-1"), self.confirm("OPS-1")])
        self.assertFalse(decision.approved)

    def test_requester_cannot_verify_own_change(self) -> None:
        decision = decide_account_change(
            self.make_request(), [self.confirm("OPS-3301"), self.confirm("OPS-2")]
        )
        self.assertFalse(decision.approved)


class AffectedScopeTests(unittest.TestCase):
    def test_episode_correction_reaches_downstream_facts(self) -> None:
        self.assertEqual(
            ("charge_item", "complication_group", "fund_settlement", "personal_payment"),
            affected_fact_types("delivery_episode"),
        )

    def test_eligibility_change_reaches_allowance_and_payment(self) -> None:
        self.assertEqual(
            ("allowance_entitlement", "personal_payment"),
            affected_fact_types("insured_person"),
        )

    def test_leaf_fact_affects_nothing(self) -> None:
        self.assertEqual((), affected_fact_types("payout_account"))


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.events = json.loads((ROOT / "data" / "sample_flow.json").read_text(encoding="utf-8"))
        cls.state = replay(cls.events)

    def test_duplicate_acceptance_is_intercepted(self) -> None:
        key = "MBS-6e2ec829e0572dd1ea144ca6"
        self.assertEqual((key,), self.state.accepted_keys)
        self.assertEqual((key,), self.state.duplicates)

    def test_business_key_matches_derived_facts(self) -> None:
        accepted = next(e for e in self.events if e["event_type"] == "CASE_ACCEPTED")
        facts = accepted["payload"]
        derived = derive_business_key(
            insured_id=facts["insured_id"],
            facility_id=facts["facility_id"],
            episode_no=facts["episode_no"],
            benefit_kind=facts["benefit_kind"],
        )
        self.assertEqual(accepted["business_key"], derived)

    def test_adjustments_never_overwrite_original_flow(self) -> None:
        flows = {flow.flow_id: flow for flow in self.state.flows}
        self.assertEqual(640000, flows["FL-2026-0001"].amount_fen)
        self.assertEqual("original", flows["FL-2026-0001"].kind)
        self.assertEqual("FL-2026-0001", flows["FL-2026-0003"].references)
        self.assertEqual(ADJUSTMENT_REVERSAL, flows["FL-2026-0003"].kind)
        self.assertEqual("FL-2026-0002", flows["FL-2026-0004"].references)
        self.assertEqual(ADJUSTMENT_INSTALLMENT, flows["FL-2026-0004"].kind)

    def test_checkpoints_are_recorded_per_flow(self) -> None:
        self.assertEqual((1,), self.state.checkpoints["FS-2026-0001"])
        self.assertEqual((1,), self.state.checkpoints["ALW-2026-0001"])

    def test_replay_is_deterministic_for_recompute(self) -> None:
        self.assertEqual(self.state, replay(list(self.events)))

    def test_no_issues_in_sample_flow(self) -> None:
        self.assertEqual((), self.state.issues)

    def test_duplicate_checkpoint_is_reported_not_executed(self) -> None:
        events = [
            {
                "event_type": "PAYMENT_POSTED",
                "aggregate_id": "FS-1",
                "payload": {"flow_id": "FL-1", "amount_fen": 100, "checkpoint_seq": 1},
            },
            {
                "event_type": "PAYMENT_POSTED",
                "aggregate_id": "FS-1",
                "payload": {"flow_id": "FL-2", "amount_fen": 100, "checkpoint_seq": 1},
            },
        ]
        state = replay(events)
        self.assertEqual((1,), state.checkpoints["FS-1"])
        self.assertEqual(1, len(state.issues))
        self.assertIn("重复", state.issues[0])

    def test_adjustment_to_unknown_original_is_reported(self) -> None:
        events = [
            {
                "event_type": "ADJUSTMENT_APPLIED",
                "aggregate_id": "FS-1",
                "payload": {"flow_id": "FL-9", "kind": "reversal", "original_flow_id": "FL-X", "amount_fen": 1},
            }
        ]
        state = replay(events)
        self.assertEqual(1, len(state.issues))
        self.assertIn("FL-X", state.issues[0])

    def test_family_progress_view_tracks_each_subject(self) -> None:
        progress = {row["subject"]: row["stage"] for row in progress_view(self.events)}
        self.assertEqual("费用已归类", progress["CHG-2026-0001"])
        self.assertEqual("结算已核定", progress["CASE-2026-0926-001"])
        self.assertEqual("待遇已调整", progress["ALW-2026-0001"])

    def test_correction_declares_only_affected_fact_types(self) -> None:
        correction = next(e for e in self.events if e["event_type"] == "CORRECTION_APPLIED")
        declared = tuple(sorted(correction["payload"]["affected_fact_types"]))
        self.assertEqual(affected_fact_types("delivery_episode"), declared)

    def test_clearing_report_supports_operator_recompute(self) -> None:
        rows = {
            (row["payer_region"], row["payee_region"], row["kind"]): row["amount_fen"]
            for row in clearing_report(self.state)
        }
        self.assertEqual(640000, rows[("330106", "320505", "original")])
        self.assertEqual(60000, rows[("330106", "320505", ADJUSTMENT_REVERSAL)])
        self.assertEqual(520000, rows[("330106", "330106", "original")])
        self.assertEqual(120000, rows[("330106", "330106", ADJUSTMENT_INSTALLMENT)])


if __name__ == "__main__":
    unittest.main()
