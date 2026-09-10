'TCP capture adapter for task-card images sent by VisionMaster.'

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .models import VisionMeasurement
from .vm_protocol import parse_vm_trigger_response

TASK_CARD_REQUESTS = {
    1: "duqu",
    2: "duqu",
}
VM_REQUEST_PREFIXES = frozenset({"wukuai", "tuopan"})


class VisionMasterConnectionError(ConnectionError):
    'Raised when the configured VisionMaster TCP service cannot be reached.'
    pass


class VisionMasterImageError(ValueError):
    'Raised when VisionMaster connects but does not provide one valid payload.'
    pass


@dataclass(frozen=True, slots=True)
class VisionMasterTcpConfig:

    'Known VisionMaster server settings with safe receive limits.'
    host: str = "127.0.0.1"
    port: int = 7930
    connect_timeout_s: float = 5.0
    idle_timeout_s: float = 1.0
    max_image_bytes: int = 20 * 1024 * 1024
    chunk_size: int = 64 * 1024
    task_card_payload_encoding: str = "local_image_path"

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("VisionMaster 主机地址不能为空。")
        if not 1 <= self.port <= 65535:
            raise ValueError("VisionMaster 端口必须在 1 到 65535 之间。")
        if self.connect_timeout_s <= 0 or self.idle_timeout_s <= 0:
            raise ValueError("VisionMaster 超时必须大于 0。")
        if self.max_image_bytes <= 0 or self.chunk_size <= 0:
            raise ValueError("VisionMaster 图像大小和分块大小必须大于 0。")
        if self.task_card_payload_encoding != "local_image_path":
            raise ValueError("任务卡图片传输格式必须为 local_image_path。")


@dataclass(frozen=True, slots=True)
class ReceivedTaskImage:

    'Raw task-card payload captured from exactly one VisionMaster connection.'
    data: bytes
    received_at: datetime
    host: str
    port: int

    @property
    def suffix(self) -> str:
        return detect_image_suffix(self.data)


@dataclass(frozen=True, slots=True)
class VisionMasterCollectionConfig:

    'Automatic two-card collection settings for the competition flow.'
    tcp: VisionMasterTcpConfig = VisionMasterTcpConfig()
    retry_interval_s: float = 0.5
    collection_timeout_s: float = 120.0

    def __post_init__(self) -> None:
        if self.retry_interval_s <= 0:
            raise ValueError("VisionMaster 重连间隔必须大于 0。")
        if self.collection_timeout_s <= 0:
            raise ValueError("VisionMaster 收集超时必须大于 0。")


@dataclass(frozen=True, slots=True)
class ReceivedTaskCards:

    'Two task-card images automatically labeled by their arrival order.'
    card1: ReceivedTaskImage
    card2: ReceivedTaskImage


class VisionMasterTcpImageClient:

    'Request one VM result, then capture its response on the same connection.\n\n    The host sends one exact UTF-8 request string. VisionMaster handles that\n    request through a receive event and returns exactly one payload. The\n    response ends when VisionMaster closes the connection, or when no more bytes\n    arrive before the configured idle timeout.\n    '
    def __init__(self, config: VisionMasterTcpConfig) -> None:
        self._config = config

    def request_payload(self, request: str) -> bytes:

        'Send one TCP request and return its untouched VM response payload.'
        normalized_request = request.strip()
        if not normalized_request or "\n" in normalized_request or "\r" in normalized_request:
            raise ValueError("VisionMaster 请求必须是一行非空文本。")

        try:
            connection = socket.create_connection(
                (self._config.host, self._config.port),
                timeout=self._config.connect_timeout_s,
            )
        except OSError as exc:
            raise VisionMasterConnectionError(
                f"无法连接 VisionMaster TCP 服务 "
                f"{self._config.host}:{self._config.port}。"
            ) from exc

        chunks: list[bytes] = []
        total_bytes = 0
        with connection:
            try:
                connection.sendall(normalized_request.encode("utf-8"))
            except OSError as exc:
                raise VisionMasterConnectionError(
                    f"向 VisionMaster TCP 服务发送请求失败：{normalized_request}。"
                ) from exc
            connection.settimeout(self._config.idle_timeout_s)
            while True:
                try:
                    chunk = connection.recv(self._config.chunk_size)
                except socket.timeout:
                    if chunks:
                        break
                    raise VisionMasterImageError(
                        f"VisionMaster 未在等待时间内响应请求：{normalized_request}。"
                    ) from None

                if not chunk:
                    break

                total_bytes += len(chunk)
                if total_bytes > self._config.max_image_bytes:
                    raise VisionMasterImageError(
                        f"图像数据超过 {self._config.max_image_bytes} 字节上限。"
                    )
                chunks.append(chunk)

        payload = b"".join(chunks)
        if not payload:
            raise VisionMasterImageError(
                f"VisionMaster 在关闭连接前没有响应请求：{normalized_request}。"
            )
        return payload

    def request_task_card(self, card_number: int) -> ReceivedTaskImage:

        'Request one task card, then load its local PNG or JPEG file.'
        try:
            request = TASK_CARD_REQUESTS[card_number]
        except KeyError as exc:
            raise ValueError("任务卡编号只能是 1 或 2。") from exc

        image_data = read_task_card_from_local_path(
            self.request_payload(request),
            max_image_bytes=self._config.max_image_bytes,
        )

        return ReceivedTaskImage(
            data=image_data,
            received_at=datetime.now().astimezone(),
            host=self._config.host,
            port=self._config.port,
        )

    def request_measurement(self, prefix: str, trigger: str) -> VisionMeasurement:

        'Request one VM result using exactly ``prefix,trigger`` text.'
        normalized_prefix = prefix.strip()
        normalized_trigger = trigger.strip()
        if normalized_prefix not in VM_REQUEST_PREFIXES:
            raise ValueError("VisionMaster 请求前缀必须是 wukuai 或 tuopan。")
        if not normalized_trigger:
            raise ValueError("VisionMaster 请求必须包含一个非空触发字符。")
        return parse_vm_trigger_response(
            normalized_trigger,
            self.request_payload(f"{normalized_prefix},{normalized_trigger}"),
        )


class VisionMasterTaskCardCollector:

    'Request task cards individually in the required competition order.'
    def __init__(self, config: VisionMasterCollectionConfig) -> None:
        self._config = config
        self._client = VisionMasterTcpImageClient(config.tcp)

    def collect_card(self, card_number: int) -> ReceivedTaskImage:

        'Request one specific task card until it is received or times out.'
        deadline = time.monotonic() + self._config.collection_timeout_s
        last_error: Exception | None = None

        while True:
            try:
                return self._client.request_task_card(card_number)
            except (VisionMasterConnectionError, VisionMasterImageError) as exc:
                last_error = exc
                if time.monotonic() >= deadline:
                    raise VisionMasterImageError(
                        f"在规定时间内未能收到 VisionMaster 任务卡 {card_number}。"
                    ) from last_error
                time.sleep(self._config.retry_interval_s)

    def collect_measurement(self, prefix: str, trigger: str) -> VisionMeasurement:

        'Request and validate one block or tray result from VisionMaster.'
        return self._client.request_measurement(prefix, trigger)

    def collect_task_cards(self) -> ReceivedTaskCards:

        'Compatibility helper that requests card 1 first, then card 2.'
        return ReceivedTaskCards(
            card1=self.collect_card(1),
            card2=self.collect_card(2),
        )


def save_task_image(
    image: ReceivedTaskImage, output_dir: Path, card_number: int
) -> Path:

    'Save a raw VisionMaster payload without changing any bytes.'
    if card_number not in (1, 2):
        raise ValueError("任务卡编号只能是 1 或 2。")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = image.received_at.strftime("%Y%m%d_%H%M%S_%f")
    path = output_dir / f"task_card_{card_number}_{timestamp}{image.suffix}"
    path.write_bytes(image.data)
    return path


def save_task_cards(cards: ReceivedTaskCards, output_dir: Path) -> tuple[Path, Path]:

    'Save both automatically collected cards using their required card numbers.'
    return (
        save_task_image(cards.card1, output_dir, 1),
        save_task_image(cards.card2, output_dir, 2),
    )


def detect_image_suffix(data: bytes) -> str:

    'Return a safe file suffix for common image containers, otherwise .bin.'
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"BM"):
        return ".bmp"
    return ".bin"


def read_task_card_from_local_path(payload: bytes, *, max_image_bytes: int) -> bytes:

    'Read a VM-saved task-card image from the absolute path returned by TCP.'
    try:
        path_text = payload.decode("utf-8-sig").strip().strip('"')
    except UnicodeDecodeError as exc:
        raise VisionMasterImageError("VisionMaster 任务卡响应必须是 UTF-8 本地图片路径。") from exc

    if not path_text:
        raise VisionMasterImageError("VisionMaster 返回的任务卡图片路径为空。")

    path = Path(path_text)
    if not path.is_absolute():
        raise VisionMasterImageError("VisionMaster 必须返回任务卡图片的绝对路径。")
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise VisionMasterImageError("VisionMaster 任务卡文件必须是 JPG、JPEG 或 PNG。")

    try:
        image_data = path.read_bytes()
    except FileNotFoundError as exc:
        raise VisionMasterImageError(
            f"VisionMaster 返回的任务卡文件不存在：{path}。"
        ) from exc
    except OSError as exc:
        raise VisionMasterImageError(
            f"无法读取 VisionMaster 任务卡文件：{path}。"
        ) from exc

    if len(image_data) > max_image_bytes:
        raise VisionMasterImageError(
            f"任务卡图片超过 {max_image_bytes} 字节上限：{path}。"
        )
    if detect_image_suffix(image_data) not in {".png", ".jpg"}:
        raise VisionMasterImageError("VisionMaster 任务卡文件不是有效的 PNG 或 JPG 图片。")
    return image_data
