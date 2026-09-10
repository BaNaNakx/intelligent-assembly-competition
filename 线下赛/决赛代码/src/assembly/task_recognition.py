from __future__ import annotations

from typing import Protocol

from .offline_state import OfflineTaskKind
from .qwen_vision import QwenResponseError
from .visionmaster_tcp import ReceivedTaskImage


class TaskClassifierPort(Protocol):
    def identify_task_card(self, image: ReceivedTaskImage) -> OfflineTaskKind: ...


class TaskRecognitionModule:
    def __init__(self, classifier: TaskClassifierPort) -> None:
        self._classifier = classifier

    def recognize(
        self,
        image: ReceivedTaskImage,
        expected_task: OfflineTaskKind | None = None,
    ) -> OfflineTaskKind:
        recognized = self._classifier.identify_task_card(image)
        if expected_task is not None and recognized is not expected_task:
            raise QwenResponseError(
                f"期望{expected_task.value}，但当前任务卡被识别为{recognized.value}。"
            )
        return recognized
