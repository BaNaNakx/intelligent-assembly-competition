from __future__ import annotations

import json
import unittest
from datetime import datetime
from typing import Mapping

from assembly.qwen_vision import ModelStreamEvent, QwenResponseError, QwenVisionClient
from assembly.offline_state import OfflineTaskKind
from assembly.settings import QwenVisionConfig
from assembly.visionmaster_tcp import ReceivedTaskImage


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"task-card"
SIX_STEP_INSTRUCTION = (
    "先把红色方块放到黄色托盘上，再把绿色方块放到红色托盘上，"
    "接着把橙色方块放到蓝色托盘上，然后把蓝色方块放到紫色托盘上，"
    "再把黄色方块放到橙色托盘上，最后把紫色方块放到绿色托盘上。"
)
SIX_STEP_INSTRUCTION += "然后把粉色方块叠放在红色方块上。"


class FakePoster:
    def __init__(self, responses: list[Mapping[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Mapping[str, str], Mapping[str, object], float]] = []

    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
    ) -> Mapping[str, object]:
        self.calls.append((url, headers, payload, timeout_s))
        return self.responses.pop(0)


class FakeStreamingPoster(FakePoster):
    def __init__(self, events: list[Mapping[str, object]]) -> None:
        super().__init__([])
        self.events = events
        self.stream_payload: Mapping[str, object] | None = None

    def stream_json(self, url, headers, payload, timeout_s, on_event) -> None:
        self.stream_payload = payload
        for event in self.events:
            on_event(event)


class FakeSequentialStreamingPoster(FakePoster):
    def __init__(self, event_batches: list[list[Mapping[str, object]]]) -> None:
        super().__init__([])
        self.event_batches = event_batches
        self.stream_payloads: list[Mapping[str, object]] = []

    def stream_json(self, url, headers, payload, timeout_s, on_event) -> None:
        self.stream_payloads.append(payload)
        for event in self.event_batches.pop(0):
            on_event(event)


def _model_response(content: Mapping[str, object]) -> Mapping[str, object]:
    return {"choices": [{"message": {"content": json.dumps(content)}}]}


def _config() -> QwenVisionConfig:
    return QwenVisionConfig(
        base_url="https://dashscope.example/compatible-mode/v1",
        model="qwen3.7-plus",
        request_timeout_s=30,
    )


def _image() -> ReceivedTaskImage:
    return ReceivedTaskImage(
        data=PNG_BYTES,
        received_at=datetime.now().astimezone(),
        host="127.0.0.1",
        port=7930,
    )


class QwenVisionClientTests(unittest.TestCase):
    def test_identifies_randomly_issued_task_card_number(self) -> None:
        client = QwenVisionClient(
            _config(),
            "test-key",
            poster=FakePoster([_model_response({"task_number": 2})]),
        )

        self.assertEqual(client.identify_task_card(_image()), OfflineTaskKind.TASK2)

    def test_rejects_unrecognized_task_card(self) -> None:
        client = QwenVisionClient(
            _config(),
            "test-key",
            poster=FakePoster([_model_response({"task_number": 0})]),
        )

        with self.assertRaisesRegex(QwenResponseError, "任务卡1还是任务卡2"):
            client.identify_task_card(_image())

    def test_streams_real_reasoning_and_final_json_when_observer_is_present(self) -> None:
        final_json = json.dumps(
            {
                "scene_category": "生活用品",
                "summary": "识别到六个物体。",
                "objects": ["电脑", "齿轮", "雨伞", "螺丝刀", "螺母", "水杯"],
            },
            ensure_ascii=False,
        )
        poster = FakeStreamingPoster(
            [
                {"choices": [{"delta": {"reasoning_content": "正在观察图片"}}]},
                {"choices": [{"delta": {"content": final_json}}]},
            ]
        )
        events: list[ModelStreamEvent] = []
        client = QwenVisionClient(
            _config(), "test-key", poster=poster, stream_observer=events.append
        )

        scene = client.analyze_task1(_image())

        self.assertEqual(scene.summary, "识别到六个物体。")
        self.assertEqual(scene.scene_category, "生活用品")
        self.assertEqual(scene.reasoning_text, "正在观察图片")
        self.assertEqual(len(scene.objects), 6)
        self.assertTrue(any(event.kind == "推理" for event in events))
        self.assertTrue(any(event.kind == "结果生成" for event in events))
        final_event = next(event for event in events if event.kind == "最终输出")
        self.assertIn("【任务一｜最终输出】", final_event.text)
        self.assertIn("场景类别：生活用品", final_event.text)
        self.assertIn("6. 水杯", final_event.text)
        self.assertIsNotNone(poster.stream_payload)
        assert poster.stream_payload is not None
        self.assertTrue(poster.stream_payload["stream"])
        self.assertTrue(poster.stream_payload["enable_thinking"])
        self.assertEqual(poster.stream_payload["thinking_budget"], 1024)
        self.assertEqual(poster.stream_payload["max_tokens"], 1536)

    def test_sends_task_card_image_as_data_url_and_parses_scene_json(self) -> None:
        poster = FakePoster(
            [
                _model_response(
                    {
                        "scene_category": "生活用品",
                        "summary": "场景中有笔记本电脑、齿轮、雨伞、螺丝刀、螺母和水杯。",
                        "objects": [
                            "笔记本电脑",
                            "齿轮",
                            "雨伞",
                            "螺丝刀",
                            "螺母",
                            "水杯",
                        ],
                    }
                )
            ]
        )
        client = QwenVisionClient(_config(), "test-key", poster=poster)

        scene = client.analyze_task1(_image())

        self.assertIn("笔记本电脑", scene.summary)
        self.assertEqual(len(scene.objects), 6)
        url, headers, payload, timeout_s = poster.calls[0]
        self.assertEqual(url, "https://dashscope.example/compatible-mode/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-key")
        self.assertEqual(timeout_s, 30)
        self.assertEqual(payload["model"], "qwen3.7-plus")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertIs(payload["enable_thinking"], False)
        user_message = payload["messages"][1]  
        image_url = user_message["content"][0]["image_url"]["url"]  
        self.assertTrue(image_url.startswith("data:image/png;base64,"))

    def test_parses_seven_step_instruction_with_deterministic_trigger_mapping(self) -> None:
        poster = FakePoster([_model_response({"instruction": SIX_STEP_INSTRUCTION})])
        client = QwenVisionClient(_config(), "test-key", poster=poster)

        plan = client.analyze_task2(_image(), task1_summary="场景识别完成。")

        self.assertEqual(
            tuple(step.agent_trigger for step in plan.steps),
            ("11 23", "14 21", "12 25", "15 26", "13 22", "16 24", "18 11"),
        )

    def test_task2_does_not_require_task1_to_run_first(self) -> None:
        poster = FakePoster([_model_response({"instruction": SIX_STEP_INSTRUCTION})])
        client = QwenVisionClient(_config(), "test-key", poster=poster)

        plan = client.analyze_task2(_image())

        self.assertEqual(len(plan.steps), 7)
        self.assertEqual(plan.task1_summary, "")

    def test_parses_a_different_random_task_card_order_without_hardcoding(self) -> None:
        instruction = (
            "先把紫色方块放到红色托盘上，再把蓝色方块放到橙色托盘上，"
            "接着把绿色方块放到黄色托盘上，然后把黄色方块放到蓝色托盘上，"
            "再把橙色方块放到紫色托盘上，最后把红色方块放到绿色托盘上，"
            "然后把青色方块叠放在紫色方块上"
        )
        client = QwenVisionClient(
            _config(), "test-key", poster=FakePoster([_model_response({"instruction": instruction})])
        )

        plan = client.analyze_task2(_image(), task1_summary="场景识别完成。")

        self.assertEqual(
            tuple(step.agent_trigger for step in plan.steps),
            ("16 21", "15 22", "14 23", "13 25", "12 26", "11 24", "17 16"),
        )

    def test_rejects_non_json_after_one_automatic_retry(self) -> None:
        poster = FakePoster(
            [
                {"choices": [{"message": {"content": "not json"}}]},
                {"choices": [{"message": {"content": "still not json"}}]},
            ]
        )
        client = QwenVisionClient(_config(), "test-key", poster=poster)
        with self.assertRaisesRegex(QwenResponseError, "自动复核"):
            client.analyze_task1(_image())
        self.assertEqual(len(poster.calls), 2)

    def test_retries_invalid_task1_result_once_with_higher_limits(self) -> None:
        valid = {
            "scene_category": "生活用品",
            "summary": "识别到六个物体。",
            "objects": ["电脑", "齿轮", "雨伞", "螺丝刀", "螺母", "水杯"],
        }
        poster = FakePoster(
            [
                _model_response(
                    {
                        "scene_category": "生活用品",
                        "summary": "识别到齿轮。",
                        "objects": ["齿轮"],
                    }
                ),
                _model_response(valid),
            ]
        )
        client = QwenVisionClient(
            _config(),
            "test-key",
            poster=poster,
        )

        scene = client.analyze_task1(_image())

        self.assertEqual(scene.objects, tuple(valid["objects"]))
        self.assertEqual(len(poster.calls), 2)
        retry_payload = poster.calls[1][2]
        self.assertEqual(retry_payload["max_tokens"], 2048)
        retry_prompt = retry_payload["messages"][1]["content"][1]["text"]
        self.assertIn("恰好包含六项", retry_prompt)

    def test_rejects_duplicate_or_placeholder_objects_after_retry(self) -> None:
        invalid_results = [
            {
                "scene_category": "生活用品",
                "summary": "识别完成。",
                "objects": ["电脑", "电脑", "雨伞", "螺丝刀", "螺母", "水杯"],
            },
            {
                "scene_category": "生活用品",
                "summary": "识别完成。",
                "objects": ["电脑", "齿轮", "雨伞", "螺丝刀", "螺母", "未知物体"],
            },
        ]
        client = QwenVisionClient(
            _config(),
            "test-key",
            poster=FakePoster([_model_response(item) for item in invalid_results]),
        )

        with self.assertRaisesRegex(QwenResponseError, "占位"):
            client.analyze_task1(_image())

    def test_streaming_retry_preserves_both_reasoning_and_raises_budget(self) -> None:
        invalid_json = json.dumps(
            {
                "scene_category": "生活用品",
                "summary": "只识别到一个物体。",
                "objects": ["齿轮"],
            },
            ensure_ascii=False,
        )
        valid_json = json.dumps(
            {
                "scene_category": "生活用品",
                "summary": "识别到六个物体。",
                "objects": ["电脑", "齿轮", "雨伞", "螺丝刀", "螺母", "水杯"],
            },
            ensure_ascii=False,
        )
        poster = FakeSequentialStreamingPoster(
            [
                [
                    {"choices": [{"delta": {"reasoning_content": "首次识别。"}}]},
                    {"choices": [{"delta": {"content": invalid_json}}]},
                ],
                [
                    {"choices": [{"delta": {"reasoning_content": "重新检查六个区域。"}}]},
                    {"choices": [{"delta": {"content": valid_json}}]},
                ],
            ]
        )
        events: list[ModelStreamEvent] = []
        client = QwenVisionClient(
            _config(), "test-key", poster=poster, stream_observer=events.append
        )

        scene = client.analyze_task1(_image())

        self.assertEqual(len(scene.objects), 6)
        self.assertIn("首次识别。", scene.reasoning_text)
        self.assertIn("【自动复核推理】", scene.reasoning_text)
        self.assertIn("重新检查六个区域。", scene.reasoning_text)
        self.assertEqual(poster.stream_payloads[0]["thinking_budget"], 1024)
        self.assertEqual(poster.stream_payloads[1]["thinking_budget"], 1536)
        self.assertTrue(any(event.kind == "校验" for event in events))
