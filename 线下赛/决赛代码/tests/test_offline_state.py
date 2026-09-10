import unittest

from assembly.offline_state import (
    OfflineRoundPhase,
    OfflineRoundStateError,
    OfflineRoundStateMachine,
    OfflineTaskKind,
)


class OfflineRoundStateMachineTests(unittest.TestCase):
    def test_accepts_task1_then_task2(self) -> None:
        state = OfflineRoundStateMachine()

        state.begin_task(OfflineTaskKind.TASK1)
        self.assertEqual(
            state.complete_active_task(), OfflineRoundPhase.READY
        )
        state.begin_task(OfflineTaskKind.TASK2)

        self.assertEqual(state.complete_active_task(), OfflineRoundPhase.COMPLETED)

    def test_accepts_task2_then_task1(self) -> None:
        state = OfflineRoundStateMachine()

        state.begin_task(OfflineTaskKind.TASK2)
        state.complete_active_task()
        state.begin_task(OfflineTaskKind.TASK1)

        self.assertEqual(state.complete_active_task(), OfflineRoundPhase.COMPLETED)

    def test_rejects_repeating_completed_task(self) -> None:
        state = OfflineRoundStateMachine()
        state.begin_task(OfflineTaskKind.TASK1)
        state.complete_active_task()

        with self.assertRaisesRegex(OfflineRoundStateError, "已经完成"):
            state.begin_task(OfflineTaskKind.TASK1)

    def test_failure_discards_both_task_results_and_requires_restart(self) -> None:
        state = OfflineRoundStateMachine()
        state.begin_task(OfflineTaskKind.TASK1)
        state.complete_active_task()
        state.begin_task(OfflineTaskKind.TASK2)

        state.fail_round("识别失败")
        failed = state.snapshot()
        self.assertEqual(failed.phase, OfflineRoundPhase.FAILED)
        self.assertEqual(failed.completed_tasks, frozenset())
        self.assertEqual(failed.failure_reason, "识别失败")

        state.restart_round()
        restarted = state.snapshot()
        self.assertEqual(restarted.round_index, 2)
        self.assertEqual(restarted.phase, OfflineRoundPhase.READY)
        self.assertEqual(restarted.completed_tasks, frozenset())

    def test_single_task_robot_mode_completes_after_task2(self) -> None:
        state = OfflineRoundStateMachine((OfflineTaskKind.TASK2,))
        state.begin_task(OfflineTaskKind.TASK2)
        self.assertEqual(state.complete_active_task(), OfflineRoundPhase.COMPLETED)


if __name__ == "__main__":
    unittest.main()
