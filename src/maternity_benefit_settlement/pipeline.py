"""支付步骤中断后，从不可重复的检查点恢复。

每个完成的步骤记录一次检查点；同一案例同一步骤重复记录时返回原检查点，
恢复时跳过已完成步骤，从首个未完成步骤继续，保证任何一步都不会重做。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime

from .facts import business_key

DEFAULT_STEPS = ("classify", "authorize", "post_ledger", "disburse")


@dataclass(frozen=True)
class Checkpoint:
    case_id: str
    step: str
    token: str
    completed_at: datetime


class CheckpointStore:
    def __init__(self) -> None:
        self._checkpoints: dict[tuple[str, str], Checkpoint] = {}

    def completed(self, case_id: str, step: str) -> Checkpoint | None:
        return self._checkpoints.get((case_id, step))

    def record(self, checkpoint: Checkpoint) -> Checkpoint:
        """同一案例同一步骤只记录一次：重复记录返回原检查点。"""
        if checkpoint.completed_at.tzinfo is None or checkpoint.completed_at.utcoffset() is None:
            raise ValueError("检查点时间必须包含时区")
        key = (checkpoint.case_id, checkpoint.step)
        existing = self._checkpoints.get(key)
        if existing is not None:
            return existing
        self._checkpoints[key] = checkpoint
        return checkpoint


def run_pipeline(
    case_id: str,
    steps: Iterable[str],
    store: CheckpointStore,
    execute: Callable[[str], None],
    completed_at: datetime,
) -> list[str]:
    """跳过已完成检查点，从首个未完成步骤继续；返回本次实际执行的步骤。"""
    ran: list[str] = []
    for step in steps:
        if store.completed(case_id, step) is not None:
            continue
        execute(step)
        store.record(
            Checkpoint(
                case_id=case_id,
                step=step,
                token=business_key("checkpoint", case_id, step),
                completed_at=completed_at,
            )
        )
        ran.append(step)
    return ran
