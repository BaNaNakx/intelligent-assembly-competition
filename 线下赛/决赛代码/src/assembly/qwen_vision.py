'Qwen 3.7 Plus visual task-card analysis adapter.'

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .models import SceneResult, TaskPlan
from .offline_state import OfflineTaskKind
from .settings import QwenVisionConfig
from .task_plan import TaskPlanValidationError, parse_assembly_instruction
from .visionmaster_tcp import ReceivedTaskImage


class QwenApiError(RuntimeError):
    'Raised when Model Studio cannot complete an API request.'
    pass


class QwenResponseError(ValueError):
    'Raised when a model response is not the requested structured result.'
    def __init__(self, message: str, *, reasoning_text: str = "") -> None:
        super().__init__(message)
        self.reasoning_text = reasoning_text


@dataclass(frozen=True, slots=True)
class ModelStreamEvent:

    'A real-time visual-model event suitable for the shared-screen log.'
    stage: str
    kind: str
    text: str


class JsonHttpPoster(Protocol):

    'Small transport seam that keeps unit tests fully offline.'
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
    ) -> Mapping[str, object]: ...


class JsonSsePoster(Protocol):
    def stream_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
        on_event: Callable[[Mapping[str, object]], None],
    ) -> None: ...


class UrllibJsonPoster:

    'Standard-library HTTP implementation for the OpenAI-compatible endpoint.'
    def post_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
    ) -> Mapping[str, object]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout_s) as response:
                response_body = response.read()
        except HTTPError as exc:
            raise QwenApiError(f"千问 API 返回 HTTP {exc.code}。") from exc
        except URLError as exc:
            raise QwenApiError("无法连接千问 API。") from exc
        except OSError as exc:
            raise QwenApiError("千问 API 网络请求失败。") from exc

        try:
            parsed = json.loads(response_body)
        except json.JSONDecodeError as exc:
            raise QwenResponseError("千问 API 返回的内容不是 JSON。") from exc
        if not isinstance(parsed, dict):
            raise QwenResponseError("千问 API 返回的 JSON 顶层必须是对象。")
        return parsed

    def stream_json(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_s: float,
        on_event: Callable[[Mapping[str, object]], None],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlopen(request, timeout=timeout_s) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    try:
                        parsed = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise QwenResponseError("千问流式响应中包含无效 JSON。") from exc
                    if not isinstance(parsed, dict):
                        raise QwenResponseError("千问流式响应顶层必须是 JSON 对象。")
                    on_event(parsed)
        except HTTPError as exc:
            raise QwenApiError(f"千问 API 返回 HTTP {exc.code}。") from exc
        except URLError as exc:
            raise QwenApiError("无法连接千问 API。") from exc
        except OSError as exc:
            raise QwenApiError("千问 API 流式网络请求失败。") from exc


class QwenVisionClient:

    'Recognize both task cards with Qwen and return validated domain objects.'
    def __init__(
        self,
        config: QwenVisionConfig,
        api_key: str,
        *,
        poster: JsonHttpPoster | None = None,
        stream_observer: Callable[[ModelStreamEvent], None] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("千问 API Key 不能为空。")
        self._config = config
        self._api_key = api_key
        self._poster = UrllibJsonPoster() if poster is None else poster
        self._stream_observer = stream_observer

    def identify_task_card(self, image: ReceivedTaskImage) -> OfflineTaskKind:
        result, _ = self._request_structured_result(
            image,
            _TASK_CLASSIFICATION_PROMPT,
            stage="任务卡分类",
            max_tokens=128,
        )
        task_number = result.get("task_number")
        if task_number == 1:
            return OfflineTaskKind.TASK1
        if task_number == 2:
            return OfflineTaskKind.TASK2
        raise QwenResponseError("大模型未能判断当前图片是任务卡1还是任务卡2。")

    def analyze_task1(self, image: ReceivedTaskImage) -> SceneResult:

        'Return a concise scene conclusion from task card 1.'
        reasoning_attempts: list[str] = []
        try:
            result, reasoning_text = self._request_structured_result(
                image,
                _TASK1_PROMPT,
                stage="任务一",
                thinking_budget=1024,
                max_tokens=1536,
            )
            reasoning_attempts.append(reasoning_text)
            scene_category, summary, objects = _validate_task1_result(result)
        except QwenResponseError as first_error:
            if first_error.reasoning_text:
                reasoning_attempts.append(first_error.reasoning_text)
            self._notify(
                "任务一",
                "校验",
                f"首次结果未通过校验：{first_error} 正在进行一次自动复核。",
            )
            retry_prompt = _TASK1_RETRY_PROMPT.format(error=str(first_error))
            try:
                result, reasoning_text = self._request_structured_result(
                    image,
                    retry_prompt,
                    stage="任务一",
                    thinking_budget=1536,
                    max_tokens=2048,
                )
                reasoning_attempts.append(reasoning_text)
                scene_category, summary, objects = _validate_task1_result(result)
            except QwenResponseError as retry_error:
                raise QwenResponseError(
                    f"任务卡 1 自动复核后仍未通过校验：{retry_error}",
                    reasoning_text=retry_error.reasoning_text,
                ) from retry_error
        reasoning_text = "\n\n【自动复核推理】\n".join(
            part for part in reasoning_attempts if part
        )
        trace = _safe_trace(
            self._config.model,
            {"scene_category": scene_category, "summary": summary, "objects": objects},
        )
        scene = SceneResult(
            summary=summary,
            model_trace=trace,
            objects=objects,
            reasoning_text=reasoning_text,
            scene_category=scene_category,
        )
        self._notify("任务一", "最终输出", format_task1_final_output(scene))
        return scene

    def analyze_task2(
        self, image: ReceivedTaskImage, *, task1_summary: str = ""
    ) -> TaskPlan:

        'Read the order on task card 2 and deterministically derive triggers.'
        result, _ = self._request_structured_result(
            image, _TASK2_PROMPT, stage="任务二"
        )
        instruction = _required_string(result, "instruction")
        try:
            steps = parse_assembly_instruction(instruction)
        except TaskPlanValidationError as exc:
            raise QwenResponseError(
                "任务卡 2 的模型结果无法解析为规范的中文装配指令。"
            ) from exc
        trace = _safe_trace(self._config.model, {"instruction": instruction})
        return TaskPlan(
            task1_summary=task1_summary,
            task2_instruction=instruction,
            steps=steps,
            model_trace=trace,
        )

    def _request_structured_result(
        self,
        image: ReceivedTaskImage,
        prompt: str,
        *,
        stage: str,
        thinking_budget: int | None = None,
        max_tokens: int | None = None,
    ) -> tuple[Mapping[str, object], str]:
        if self._stream_observer is not None:
            return self._request_streaming_result(
                image,
                prompt,
                stage=stage,
                thinking_budget=thinking_budget,
                max_tokens=max_tokens,
            )
        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是智能精密装配竞赛的视觉智能体。"
                        "只根据图片作答；不得编造；只能输出要求的 JSON 对象。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": _image_data_url(image)},
                        },
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
            "enable_thinking": False,
            "temperature": 0,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        response = self._poster.post_json(
            self._endpoint_url,
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self._config.request_timeout_s,
        )
        return _extract_structured_content(response), ""

    def _request_streaming_result(
        self,
        image: ReceivedTaskImage,
        prompt: str,
        *,
        stage: str,
        thinking_budget: int | None = None,
        max_tokens: int | None = None,
    ) -> tuple[Mapping[str, object], str]:
        stream_json = getattr(self._poster, "stream_json", None)
        if not callable(stream_json):
            raise QwenApiError("当前千问传输适配器不支持评分所需的流式推理展示。")
        self._notify(stage, "状态", "已发送任务卡图片，正在请求千问视觉推理。")
        content_parts: list[str] = []
        reasoning_parts: list[str] = []

        def handle_event(event: Mapping[str, object]) -> None:
            delta = _stream_delta(event)
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                reasoning_parts.append(reasoning)
                self._notify(stage, "推理", reasoning)
            content = delta.get("content")
            if isinstance(content, str) and content:
                content_parts.append(content)
                self._notify(stage, "结果生成", content)

        payload: dict[str, object] = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是智能精密装配竞赛的视觉智能体。"
                        "只根据图片作答；不得编造；只能输出要求的 JSON 对象。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": _image_data_url(image)},
                        },
                        {"type": "text", "text": prompt},
                    ],
                },
            ],
            "response_format": {"type": "json_object"},
            "enable_thinking": True,
            "stream": True,
            "stream_options": {"include_usage": True},
            "temperature": 0,
        }
        if thinking_budget is not None:
            payload["thinking_budget"] = thinking_budget
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        stream_json(
            self._endpoint_url,
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            payload,
            self._config.request_timeout_s,
            handle_event,
        )
        if not content_parts:
            raise QwenResponseError(
                "千问流式响应未返回最终 JSON 内容。",
                reasoning_text="".join(reasoning_parts).strip(),
            )
        self._notify(stage, "状态", "千问视觉推理完成，正在校验结构化结果。")
        try:
            parsed = json.loads("".join(content_parts))
        except json.JSONDecodeError as exc:
            raise QwenResponseError(
                "千问流式最终内容不是有效 JSON。",
                reasoning_text="".join(reasoning_parts).strip(),
            ) from exc
        if not isinstance(parsed, dict):
            raise QwenResponseError(
                "千问流式最终内容顶层必须是 JSON 对象。",
                reasoning_text="".join(reasoning_parts).strip(),
            )
        return parsed, "".join(reasoning_parts).strip()

    def _notify(self, stage: str, kind: str, text: str) -> None:
        if self._stream_observer is not None:
            self._stream_observer(ModelStreamEvent(stage, kind, text))

    @property
    def _endpoint_url(self) -> str:
        return f"{self._config.base_url.rstrip('/')}/chat/completions"


def _image_data_url(image: ReceivedTaskImage) -> str:
    mime_by_suffix = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".bmp": "image/bmp",
    }
    mime_type = mime_by_suffix.get(image.suffix)
    if mime_type is None:
        raise QwenResponseError(
            "VisionMaster 任务卡不是已识别的 PNG、JPG 或 BMP 图像。"
        )
    encoded = base64.b64encode(image.data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _extract_structured_content(response: Mapping[str, object]) -> Mapping[str, object]:
    try:
        choices = response["choices"]
        choice = choices[0]  
        message = choice["message"]  
        content = message["content"]  
    except (KeyError, IndexError, TypeError) as exc:
        raise QwenResponseError("千问响应缺少 choices[0].message.content。") from exc

    if not isinstance(content, str):
        raise QwenResponseError("千问响应 content 必须是 JSON 字符串。")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise QwenResponseError("千问响应 content 不是有效 JSON。") from exc
    if not isinstance(parsed, dict):
        raise QwenResponseError("千问响应 content 顶层必须是 JSON 对象。")
    return parsed


def _stream_delta(event: Mapping[str, object]) -> Mapping[str, object]:
    try:
        choices = event["choices"]
        choice = choices[0]  
        delta = choice["delta"]  
    except (KeyError, IndexError, TypeError):
        return {}
    return delta if isinstance(delta, Mapping) else {}


def _required_string(result: Mapping[str, object], name: str) -> str:
    value = result.get(name)
    if not isinstance(value, str) or not value.strip():
        raise QwenResponseError(f"千问结构化结果缺少非空字符串字段 {name}。")
    return value.strip()


def _validate_task1_result(
    result: Mapping[str, object],
) -> tuple[str, str, tuple[str, ...]]:
    scene_category = _required_string(result, "scene_category")
    summary = _required_string(result, "summary")
    raw_objects = result.get("objects")
    if not isinstance(raw_objects, list) or len(raw_objects) != 6:
        raise QwenResponseError("任务卡 1 的 objects 必须恰好包含六项。")
    if not all(isinstance(item, str) and item.strip() for item in raw_objects):
        raise QwenResponseError("任务卡 1 的六个物体名称均必须为非空字符串。")
    objects = tuple(item.strip() for item in raw_objects)
    normalized = tuple(item.casefold() for item in objects)
    if len(set(normalized)) != 6:
        raise QwenResponseError("任务卡 1 的六个物体名称不得重复。")
    placeholders = {"未知", "未知物体", "物体", "无法识别", "无法判断"}
    if any(
        item in placeholders or re.fullmatch(r"物体\d+", item)
        for item in normalized
    ):
        raise QwenResponseError("任务卡 1 的识别结果包含未知或占位物体名称。")
    return scene_category, summary, objects


def _safe_trace(model: str, result: Mapping[str, object]) -> str:

    'Keep only requested JSON fields; never persist API keys or model reasoning.'
    return json.dumps(
        {"provider": "dashscope", "model": model, "result": result},
        ensure_ascii=False,
        sort_keys=True,
    )


def format_task1_final_output(result: SceneResult) -> str:
    lines = [
        "【任务一｜最终输出】",
        "",
        f"场景类别：{result.scene_category}",
        "",
        "识别结果：",
    ]
    lines.extend(
        f"{index}. {item}" for index, item in enumerate(result.objects, start=1)
    )
    lines.extend(("", "结果校验：数量为6，名称无重复，识别有效。"))
    return "\n".join(lines)


_TASK1_PROMPT = """
分析图片中的任务卡 1。识别任务卡要求描述的场景物体。
思考过程必须简洁、完整，并依次覆盖以下七部分：
1. 任务类型判断；
2. 按图片实际布局进行画面区域扫描；
3. 逐个说明六个物体的视觉特征及判断依据；
4. 数量校验；
5. 重复性校验；
6. 按任务卡实际排列顺序整理；
7. 最终判断。
不要为了凑字数重复描述；图片并非上三下三时，必须按实际布局说明。
只输出 JSON，格式必须为：
{"scene_category":"不超过十个字的场景类别","summary":"一句完整中文场景结论","objects":["物体1","物体2","物体3","物体4","物体5","物体6"]}
objects 必须按任务卡顺序包含六个不同物体；不得遗漏、合并或使用 Markdown。
""".strip()

_TASK_CLASSIFICATION_PROMPT = """
判断图片标题和主体内容属于任务卡1还是任务卡2。
任务卡1包含六个生活中常见物体的图片；任务卡2包含七条彩色方块装配指令。
只输出JSON：{"task_number":1}或{"task_number":2}。无法可靠判断时输出{"task_number":0}，不得猜测。
""".strip()

_TASK1_RETRY_PROMPT = """
重新分析图片中的任务卡 1。首次结果存在以下问题：{error}
必须重新观察图片，不得照抄首次结果。
思考过程继续按照以下七部分简洁展开：任务类型判断、画面区域扫描、六个物体特征判断、数量校验、重复性校验、顺序整理、最终判断。
只输出 JSON，格式必须为：
{{"scene_category":"不超过十个字的场景类别","summary":"一句完整中文场景结论","objects":["物体1","物体2","物体3","物体4","物体5","物体6"]}}
objects 必须按图片实际排列顺序恰好包含六个非空且互不重复的明确物体名称；不得使用“未知物体”等占位名称，不得使用 Markdown。
""".strip()

_TASK2_PROMPT = """
分析图片中的任务卡 2。读取其完整中文装配顺序。
物块颜色共有红、橙、黄、绿、蓝、紫、青、粉、棕九种，托盘只有红、橙、黄、绿、蓝、紫六种。
前六步必须逐条写成“某色方块放到某色托盘上”，第七步必须写成“某色方块放在某色方块上”。
只输出 JSON，格式必须为：
{"instruction":"完整原始中文装配指令"}
instruction 中必须按图片顺序完整写出七条装配指令；不得遗漏第七步叠放，不得使用 Markdown。
""".strip()
