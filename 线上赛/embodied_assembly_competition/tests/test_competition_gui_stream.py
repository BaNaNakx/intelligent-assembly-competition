import unittest

from assembly.competition_gui import _format_timed_stream_fragment


class CompetitionGuiStreamTests(unittest.TestCase):
    def test_prefixes_each_nonempty_reasoning_line_once(self) -> None:
        first, line_start = _format_timed_stream_fragment(
            "1. 任务类型",
            5,
            True,
        )
        second, line_start = _format_timed_stream_fragment(
            "判断\n\n2. 画面扫描\n",
            7,
            line_start,
        )

        self.assertEqual(
            first + second,
            "[0m5s] 1. 任务类型判断\n\n[0m7s] 2. 画面扫描\n",
        )
        self.assertTrue(line_start)

    def test_continuation_fragment_does_not_repeat_timestamp(self) -> None:
        text, line_start = _format_timed_stream_fragment("继续推理", 63, False)

        self.assertEqual(text, "继续推理")
        self.assertFalse(line_start)


if __name__ == "__main__":
    unittest.main()
