'TCP capture adapter for task-card images sent by VisionMaster.'

from __future__ import annotations

import socket
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from threading import Event

from PIL import Image

from .models import VisionMeasurement
from .vm_protocol import parse_vm_trigger_response

TASK_CARD_TRIGGERS = {1: "99", 2: "99"}


def measurement_request(trigger: str) -> str:
    if trigger in {str(number) for number in range(11, 20)}:
        return f"wukuai,{trigger}"
    if trigger in {str(number) for number in range(21, 27)}:
        return f"tuopan,{trigger}"
    raise ValueError("物块编号必须为11–19，托盘编号必须为21–26。")


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
    task_card_image_directory: str = "C:/VisionMaster/task_cards"
    task_card_capture_timeout_s: float = 15.0

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("VisionMaster 主机地址不能为空。")
        if not 1 <= self.port <= 65535:
            raise ValueError("VisionMaster 端口必须在 1 到 65535 之间。")
        if self.connect_timeout_s <= 0 or self.idle_timeout_s <= 0:
            raise ValueError("VisionMaster 超时必须大于 0。")
        if self.max_image_bytes <= 0 or self.chunk_size <= 0:
            raise ValueError("VisionMaster 图像大小和分块大小必须大于 0。")
        if not self.task_card_image_directory.strip():
            raise ValueError("VisionMaster 任务卡图片文件夹不能为空。")
        if not Path(self.task_card_image_directory).is_absolute():
            raise ValueError("VisionMaster 任务卡图片文件夹必须是绝对路径。")
        if not 0 < self.task_card_capture_timeout_s < float("inf"):
            raise ValueError("VisionMaster 拍照确认超时必须是有限正数。")


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
    def __init__(self, config: VisionMasterTcpConfig, cancel_event: Event | None = None) -> None:
        self._config = config
        self._cancel_event = cancel_event or Event()

    def check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise RuntimeError("VM 流程已重置，拒绝后续请求。")

    def request_payload(self, request: str) -> bytes:

        'Send one TCP request and return its untouched VM response payload.'
        self.check_cancelled()
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
                self.check_cancelled()
                connection.sendall(normalized_request.encode("utf-8"))
            except OSError as exc:
                raise VisionMasterConnectionError(
                    f"向 VisionMaster TCP 服务发送请求失败：{normalized_request}。"
                ) from exc
            connection.settimeout(self._config.idle_timeout_s)
            while True:
                self.check_cancelled()
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

    def send_request(self, request: str) -> None:

        'Send one exact TCP request without waiting for a VM response.'
        self.check_cancelled()
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
        with connection:
            try:
                self.check_cancelled()
                connection.sendall(normalized_request.encode("utf-8"))
            except OSError as exc:
                raise VisionMasterConnectionError(
                    f"向 VisionMaster TCP 服务发送请求失败：{normalized_request}。"
                ) from exc

    def _request_capture_ack(self, deadline: float) -> None:
        self.check_cancelled()
        try:
            with socket.create_connection(
                (self._config.host, self._config.port),
                timeout=min(self._config.connect_timeout_s, max(0.01, deadline - time.monotonic())),
            ) as connection:
                self.check_cancelled()
                connection.sendall(b"duqu,99")
                response = b""
                while response != b"0011":
                    self.check_cancelled()
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise VisionMasterImageError("等待 VM 拍照确认 0011 超时。")
                    connection.settimeout(min(0.1, remaining))
                    try:
                        chunk = connection.recv(64)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise VisionMasterImageError("VM 连接已关闭，但未完整返回拍照确认 0011。")
                    response += chunk
                    if not b"0011".startswith(response):
                        raise VisionMasterImageError("VM 拍照确认必须是纯文本 0011。")
                self.check_cancelled()
        except OSError as exc:
            raise VisionMasterConnectionError("VM 拍照连接失败或通信中断。") from exc

    def request_task_card(self, card_number: int) -> ReceivedTaskImage:
        'Wait for the capture acknowledgement, then read this capture\'s newest complete image.'
        if card_number not in (1, 2):
            raise ValueError("任务卡编号只能是 1 或 2。")
        self.check_cancelled()
        directory = Path(self._config.task_card_image_directory)
        if not directory.is_dir():
            raise VisionMasterImageError(f"任务卡图片文件夹不存在：{directory}。")
        before = task_card_file_versions(directory)
        deadline = time.monotonic() + self._config.task_card_capture_timeout_s
        self._request_capture_ack(deadline)
        candidate = None
        last_error = "没有发现本次拍照新生成的任务卡图片。"
        while time.monotonic() < deadline:
            self.check_cancelled()
            current = task_card_file_versions(directory)
            changed = [path for path in current if before.get(path) != current[path]]
            if changed:
                path = max(changed, key=lambda item: (current[item][0], item.name.lower()))
                version = (path, current[path])
                if version == candidate:
                    if current[path][1] > self._config.max_image_bytes:
                        raise VisionMasterImageError("任务卡图片超过允许的字节上限。")
                    try:
                        image_data = read_task_card_file(path, max_image_bytes=self._config.max_image_bytes)
                        after = path.stat()
                        if (after.st_mtime_ns, after.st_size) == current[path]:
                            self.check_cancelled()
                            return ReceivedTaskImage(
                                data=image_data,
                                received_at=datetime.now().astimezone(),
                                host=self._config.host,
                                port=self._config.port,
                            )
                    except (VisionMasterImageError, OSError) as exc:
                        last_error = str(exc)
                candidate = version
            self._cancel_event.wait(0.1)
        self.check_cancelled()
        raise VisionMasterImageError(f"已收到 0011，但等待新图片就绪超时：{last_error}")

    def request_measurement(self, trigger: str) -> VisionMeasurement:

        'Send one unadorned ASCII command with a block or tray prefix.'
        normalized_trigger = trigger.strip()
        if not normalized_trigger:
            raise ValueError("VisionMaster 请求必须包含一个非空触发字符。")
        return parse_vm_trigger_response(
            normalized_trigger,
            self.request_payload(measurement_request(normalized_trigger)),
        )


class VisionMasterTaskCardCollector:

    'Request task cards individually in the required competition order.'
    def __init__(self, config: VisionMasterCollectionConfig, cancel_event: Event | None = None) -> None:
        self._config = config
        self._cancel_event = cancel_event or Event()
        self._client = VisionMasterTcpImageClient(config.tcp, self._cancel_event)

    def collect_card(self, card_number: int) -> ReceivedTaskImage:

        'Request one specific task card until it is received or times out.'
        return self._client.request_task_card(card_number)

    def collect_measurement(self, trigger: str) -> VisionMeasurement:

        'Request and validate one block or tray result from VisionMaster.'
        return self._client.request_measurement(trigger)

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


def read_task_card_file(path: Path, *, max_image_bytes: int) -> bytes:

    'Read the configured VM-saved task-card image.'
    if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
        raise VisionMasterImageError("VisionMaster 任务卡文件必须是 JPG、JPEG 或 PNG。")

    try:
        image_data = path.read_bytes()
    except FileNotFoundError as exc:
        raise VisionMasterImageError(
            f"等待结束后未找到 VisionMaster 任务卡文件：{path}。"
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
    try:
        with Image.open(BytesIO(image_data)) as image:
            image.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise VisionMasterImageError("任务卡图片尚未写完或文件损坏。") from exc
    return image_data


def task_card_file_versions(directory: Path) -> dict[Path, tuple[int, int]]:
    versions: dict[Path, tuple[int, int]] = {}
    for path in directory.iterdir():
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png"} or not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        versions[path] = (stat.st_mtime_ns, stat.st_size)
    return versions
