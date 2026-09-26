"""端到端联调：用 data/case_sample.json 走完整跨域结算与后续调整。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.accounts import (
    AccountChangeRequest,
    Confirmation,
    apply_account_change,
)
from maternity_benefit_settlement.casefile import load_case
from maternity_benefit_settlement.classification import (
    Category,
    classify_charge,
    resolve_payable,
)
from maternity_benefit_settlement.facts import business_key
from maternity_benefit_settlement.ledger import (
    EntryKind,
    Ledger,
    LedgerEntry,
    settlement_key,
)
from maternity_benefit_settlement.pipeline import (
    DEFAULT_STEPS,
    CheckpointStore,
    run_pipeline,
)
from maternity_benefit_settlement.recalculation import (
    Correction,
    affected_charges,
    apply_scope_deltas,
    scope_deltas,
)
from maternity_benefit_settlement.views import (
    family_progress,
    hospital_view,
    insurer_view,
    recompute_clearing,
    verify_clearing,
)

CN = timezone(timedelta(hours=8))
SETTLED_AT = datetime(2026, 9, 5, 10, 0, tzinfo=CN)
CORRECTED_AT = datetime(2026, 9, 25, 9, 0, tzinfo=CN)


class EndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = load_case(ROOT / "data" / "case_sample.json")
        self.episode_id = self.case.episode.episode_id

    def _classify(self, groups):
        packages = {p.package_id: p for p in self.case.packages}
        return {
            charge.charge_id: classify_charge(
                charge, self.case.policies, packages, groups, self.case.episode.hospital_region
            )
            for charge in self.case.charges
        }

    def _post_initial_settlement(self, ledger, payable_lines):
        totals: dict[str, int] = {}
        classifications = self._classify(self.case.complication_groups)
        for line in payable_lines:
            if classifications[line.charge_id].zero_self_pay:
                continue
            totals[line.scope] = totals.get(line.scope, 0) + line.amount_fen
        for scope, amount in totals.items():
            ledger.post(
                LedgerEntry(
                    entry_id=f"S-{scope}",
                    business_key=settlement_key(self.episode_id, scope),
                    kind=EntryKind.FUND_SETTLEMENT,
                    amount_fen=amount,
                    posted_at=SETTLED_AT,
                    reason="异地就医基金结算",
                    batch_id="B-2026-09-A",
                )
            )
        for allowance in self.case.allowances:
            ledger.post(
                LedgerEntry(
                    entry_id=f"P-{allowance.allowance_id}",
                    business_key=business_key("allowance", allowance.allowance_id),
                    kind=EntryKind.ALLOWANCE,
                    amount_fen=allowance.amount_fen,
                    posted_at=SETTLED_AT,
                    reason=allowance.allowance_type,
                    batch_id=allowance.batch_id,
                )
            )

    def test_full_cross_region_flow_with_late_correction(self) -> None:
        # 1. 归类与首次结算
        packages = {p.package_id: p for p in self.case.packages}
        classifications = self._classify(self.case.complication_groups)
        payable, notes = resolve_payable(
            self.case.charges,
            classifications,
            self.case.complication_groups,
            packages,
        )
        categories = {charge_id: c.category for charge_id, c in classifications.items()}
        self.assertEqual(Category.BASIC_PACKAGE, categories["C-01"])
        self.assertEqual(Category.ANALGESIA_MATERIAL, categories["C-03"])
        self.assertEqual(Category.COMPLICATION, categories["C-06"])
        self.assertEqual(Category.NON_POLICY, categories["C-05"])
        # 非政策项目给出可解释明细
        self.assertIn("X900", classifications["C-05"].explanation)

        ledger = Ledger()
        self._post_initial_settlement(ledger, payable)

        # 2. 经办复算与流水核对一致
        statement = recompute_clearing(
            self.episode_id, self.case.charges, classifications, payable
        )
        self.assertEqual([], verify_clearing(statement, ledger))
        self.assertEqual(380000, statement.zero_self_pay_fen)  # 基本服务包零自付
        self.assertEqual(200000, statement.non_policy_fen)     # 特需病房差价

        # 3. 家庭视角每项费用与待遇都有进度
        progress = family_progress(
            self.case.charges, classifications, ledger, self.episode_id, self.case.allowances
        )
        stages = {item.subject: item.stage for item in progress}
        self.assertEqual("零自付（基本服务包）", stages["顺产接生"])
        self.assertEqual("个人自付", stages["特需病房差价"])
        self.assertEqual("已结算", stages["产后出血止血术"])
        self.assertEqual("已结算", stages["生育津贴"])
        self.assertEqual("已结算", stages["育儿补贴"])

        # 医院视角不含账户信息
        hospital = hospital_view(self.case.episode, self.case.charges, classifications, ledger)
        self.assertEqual(380000, hospital.zero_self_pay_fen)

        # 参保地视角含去重说明与完整链条
        insurer = insurer_view(self.case.episode, payable, notes, ledger, self.case.allowances)
        self.assertEqual(2, len(insurer.allowances))

        # 4. 病案更正晚到：输血 K302 移出并发症病组，只重算受影响费用
        revised_groups = tuple(
            type(group)(
                group.group_code,
                group.episode_id,
                frozenset(code for code in group.item_codes if code != "K302"),
                group.diagnosed_on,
            )
            for group in self.case.complication_groups
        )
        correction = Correction("COR-1", self.episode_id, frozenset({"C-07"}), "病案更正：输血不计入病组")
        affected = affected_charges(self.case.charges, correction)
        self.assertEqual(["C-07"], [c.charge_id for c in affected])

        new_classifications = self._classify(revised_groups)
        new_payable, _ = resolve_payable(self.case.charges, new_classifications, revised_groups, packages)
        old_by_charge = {line.charge_id: line for line in payable}
        new_by_charge = {line.charge_id: line for line in new_payable}
        deltas = scope_deltas(
            [old_by_charge[c.charge_id] for c in affected if c.charge_id in old_by_charge],
            [new_by_charge[c.charge_id] for c in affected if c.charge_id in new_by_charge],
        )
        self.assertEqual({"complication": -120000}, deltas)
        posted = apply_scope_deltas(ledger, correction, deltas, CORRECTED_AT)
        self.assertEqual([EntryKind.REVERSAL], [e.kind for e in posted])

        # 原流水保留未覆盖；镇痛、津贴等其他流水不受影响
        original = ledger.by_id("S-complication")
        self.assertEqual(370000, original.amount_fen)
        self.assertEqual(250000, ledger.remaining("S-complication"))
        self.assertEqual(150000 + 60000, sum(
            e.amount_fen for e in ledger.history_by_key(settlement_key(self.episode_id, "analgesia_material"))
        ))

        # 调整后复算与流水再次一致
        new_statement = recompute_clearing(
            self.episode_id, self.case.charges, new_classifications, new_payable
        )
        self.assertEqual([], verify_clearing(new_statement, ledger))

        # 家庭进度反映冲正：仍在病组内的费用显示部分冲正，被移出的输血转为个人自付并附明细
        progress_after = family_progress(
            self.case.charges, new_classifications, ledger, self.episode_id, self.case.allowances
        )
        stages_after = {item.subject: item.stage for item in progress_after}
        self.assertEqual("已部分冲正", stages_after["产后出血止血术"])
        self.assertEqual("个人自付", stages_after["输血"])

        # 5. 若家庭已收到重复待遇，按分期追回而非整笔追回
        recoveries = ledger.recover_in_installments(
            "P-AL-2", "REC-1", 120000, 3, CORRECTED_AT, "病案更正后的多领追回"
        )
        self.assertEqual(3, len(recoveries))
        self.assertEqual(500000 - 120000, ledger.remaining("P-AL-2"))

        # 6. 中断恢复：post_ledger 中断后从检查点续跑，不重做已完成步骤
        store = CheckpointStore()
        order: list[str] = []

        def execute_once(step: str) -> None:
            order.append(step)
            if step == "post_ledger" and len(order) == 3:
                raise RuntimeError("支付通道中断")

        with self.assertRaises(RuntimeError):
            run_pipeline("CASE-1", DEFAULT_STEPS, store, execute_once, CORRECTED_AT)
        run_pipeline("CASE-1", DEFAULT_STEPS, store, lambda step: order.append(step), CORRECTED_AT)
        self.assertEqual(["classify", "authorize", "post_ledger", "post_ledger", "disburse"], order)

        # 7. 收款账户变更需双重核验
        request = AccountChangeRequest("REQ-ACC-1", "P-1001", self.case.receiving_account, CORRECTED_AT)
        with self.assertRaises(PermissionError):
            apply_account_change(request, [Confirmation("经办甲", "经办复核", CORRECTED_AT)])
        confirmed = apply_account_change(
            request,
            [
                Confirmation("经办甲", "经办复核", CORRECTED_AT),
                Confirmation("银行系统", "银行要素校验", CORRECTED_AT),
            ],
        )
        self.assertEqual("****1234", confirmed.account_no_masked)


if __name__ == "__main__":
    unittest.main()
