from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from maternity_benefit_settlement.pipeline import (
    DEFAULT_STEPS,
    Checkpoint,
    CheckpointStore,
    run_pipeline,
)

CN = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 10, 10, 0, tzinfo=CN)


class PipelineTests(unittest.TestCase):
    def test_full_run_records_every_step(self) -> None:
        store = CheckpointStore()
        ran = run_pipeline("CASE-1", DEFAULT_STEPS, store, lambda step: None, NOW)
        self.assertEqual(list(DEFAULT_STEPS), ran)
        self.assertIsNotNone(store.completed("CASE-1", "disburse"))

    def test_resume_skips_completed_steps_after_crash(self) -> None:
        store = CheckpointStore()
        executed: list[str] = []

        def crashing(step: str) -> None:
            executed.append(step)
            if step == "post_ledger":
                raise RuntimeError("网络中断")

        with self.assertRaises(RuntimeError):
            run_pipeline("CASE-1", DEFAULT_STEPS, store, crashing, NOW)
        self.assertEqual(["classify", "authorize", "post_ledger"], executed)

        executed.clear()
        ran = run_pipeline("CASE-1", DEFAULT_STEPS, store, lambda step: executed.append(step), NOW)
        # post_ledger 在中断前未记录检查点，恢复后重做；classify/authorize 不重做
        self.assertEqual(["post_ledger", "disburse"], executed)
        self.assertEqual(["post_ledger", "disburse"], ran)

    def test_recording_same_step_twice_returns_original_checkpoint(self) -> None:
        store = CheckpointStore()
        first = store.record(Checkpoint("CASE-1", "classify", "token-1", NOW))
        second = store.record(Checkpoint("CASE-1", "classify", "token-2", NOW))
        self.assertIs(first, second)
        self.assertEqual("token-1", store.completed("CASE-1", "classify").token)

    def test_checkpoint_requires_timezone(self) -> None:
        store = CheckpointStore()
        with self.assertRaises(ValueError):
            store.record(Checkpoint("CASE-1", "classify", "token", datetime(2026, 9, 10, 10, 0)))


if __name__ == "__main__":
    unittest.main()
