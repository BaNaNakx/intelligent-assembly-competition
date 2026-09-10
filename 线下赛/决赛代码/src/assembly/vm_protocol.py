'Parser and in-memory store for the fixed VisionMaster coordinate protocol.'

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import re

from .models import Color, EntityKind, VisionMeasurement


class VmProtocolError(ValueError):
    'Raised when a VisionMaster packet differs from the competition protocol.'
    pass


VM_ACK = "ACK;VM_DATA\n"

_FIXED_COORDINATES: Mapping[tuple[float, float, float], tuple[Color, EntityKind]] = {
    (1.0, -1.0, 10.0): (Color.RED, EntityKind.BLOCK),
    (2.0, -2.0, 20.0): (Color.ORANGE, EntityKind.BLOCK),
    (3.0, -3.0, 30.0): (Color.YELLOW, EntityKind.BLOCK),
    (4.0, -4.0, 40.0): (Color.GREEN, EntityKind.BLOCK),
    (5.0, -5.0, -50.0): (Color.BLUE, EntityKind.BLOCK),
    (6.0, -6.0, -60.0): (Color.PURPLE, EntityKind.BLOCK),
    (10.0, -10.0, 10.0): (Color.RED, EntityKind.TRAY),
    (20.0, -20.0, 20.0): (Color.ORANGE, EntityKind.TRAY),
    (30.0, -30.0, 30.0): (Color.YELLOW, EntityKind.TRAY),
    (40.0, -40.0, 40.0): (Color.GREEN, EntityKind.TRAY),
    (50.0, -50.0, -50.0): (Color.BLUE, EntityKind.TRAY),
    (60.0, -60.0, -60.0): (Color.PURPLE, EntityKind.TRAY),
}

_TRIGGER_TARGETS: Mapping[str, tuple[Color, EntityKind]] = {
    **{color.block_trigger: (color, EntityKind.BLOCK) for color in Color},
    **{color.tray_trigger: (color, EntityKind.TRAY) for color in Color if color.has_tray},
}


def parse_vm_measurement(
    raw_packet: str, *, received_at: datetime | None = None
) -> VisionMeasurement:

    'Parse one exact #0;X;Y;RZ packet defined by the project specification.'
    packet = raw_packet.strip()
    fields = packet[:-1].split(";") if packet.endswith(";") else packet.split(";")
    if len(fields) != 4 or fields[0] not in {"#0", "0"}:
        raise VmProtocolError("VM 报文必须使用 #0;X;Y;RZ 或 0;X;Y;RZ 格式。")

    try:
        coordinate = tuple(float(value) for value in fields[1:])
    except ValueError as exc:
        raise VmProtocolError("VM 报文中的 X、Y、RZ 必须是数字。") from exc

    if coordinate not in _FIXED_COORDINATES:
        raise VmProtocolError("VM 报文不是项目书规定的 12 组固定数据之一。")

    color, kind = _FIXED_COORDINATES[coordinate]
    return VisionMeasurement(
        color=color,
        kind=kind,
        x=coordinate[0],
        y=coordinate[1],
        rz=coordinate[2],
        raw_packet=packet,
        received_at=received_at or datetime.now(),
    )


def encode_vm_ack() -> bytes:
    return VM_ACK.encode("utf-8")


class VisionMeasurementStore:

    'Collects exactly one fixed measurement for each block and tray.'
    def __init__(self) -> None:
        self._measurements: dict[tuple[Color, EntityKind], VisionMeasurement] = {}

    def ingest(
        self, raw_packet: str, *, received_at: datetime | None = None
    ) -> VisionMeasurement:
        measurement = parse_vm_measurement(raw_packet, received_at=received_at)
        if measurement.key in self._measurements:
            raise VmProtocolError(
                f"重复的 VM 数据：{measurement.color.display_name}{measurement.kind.value}。"
            )
        self._measurements[measurement.key] = measurement
        return measurement

    @property
    def is_complete(self) -> bool:
        return len(self._measurements) == len(_FIXED_COORDINATES)

    @property
    def count(self) -> int:
        return len(self._measurements)

    def missing(self) -> tuple[tuple[Color, EntityKind], ...]:
        expected = {
            (color, kind) for color, kind in _FIXED_COORDINATES.values()
        }
        return tuple(
            sorted(
                expected.difference(self._measurements),
                key=lambda item: (item[0].value, item[1].value),
            )
        )

    def snapshot(self) -> Mapping[tuple[Color, EntityKind], VisionMeasurement]:
        return dict(self._measurements)


def fixed_vm_packets() -> tuple[str, ...]:

    'Returns all project-book packets for deterministic tests and demo input.'
    return tuple(
        f"#0;{_number(x)};{_number(y)};{_number(rz)}"
        for x, y, rz in _FIXED_COORDINATES
    )


def fixed_workspace_measurements() -> Mapping[tuple[Color, EntityKind], VisionMeasurement]:

    'Load the 12 immutable project-book work positions locally.\n\n    These positions are static competition data, not VisionMaster output.  This\n    function deliberately performs no network communication.\n    '
    now = datetime.now().astimezone()
    return {
        (color, kind): VisionMeasurement(
            color=color,
            kind=kind,
            x=x,
            y=y,
            rz=rz,
            raw_packet=f"PROJECT_BOOK_FIXED;{_number(x)};{_number(y)};{_number(rz)}",
            received_at=now,
        )
        for (x, y, rz), (color, kind) in _FIXED_COORDINATES.items()
    }


_VM_PACKET_PATTERN = re.compile(
    r"(?<![\w#])(?P<packet>#?0;[-+]?\d+(?:\.\d+)?;[-+]?\d+(?:\.\d+)?;[-+]?\d+(?:\.\d+)?)(?![\w])"
)


def extract_vm_packets(payload: bytes) -> tuple[str, ...]:

    'Extract one or more fixed packets from a UTF-8 VisionMaster payload.'
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VmProtocolError("VisionMaster 坐标数据不是 UTF-8 文本。") from exc
    packets = tuple(match.group("packet") for match in _VM_PACKET_PATTERN.finditer(text))
    if not packets:
        raise VmProtocolError("VisionMaster 数据中没有找到固定坐标报文。")
    return packets


_VM_TRIGGER_RESPONSE_PATTERN = re.compile(
    r"(?P<packet>#0;(?P<x>[-+]?\d+(?:\.\d+)?);(?P<y>[-+]?\d+(?:\.\d+)?);(?P<rz>[-+]?\d+(?:\.\d+)?);)"
)


def parse_vm_trigger_response(trigger: str, payload: bytes) -> VisionMeasurement:

    'Parse one dynamic #X;Y;RZ position for the requested target.'
    normalized_trigger = trigger.strip()
    try:
        expected_key = _TRIGGER_TARGETS[normalized_trigger]
    except KeyError as exc:
        raise VmProtocolError("智能体触发字符必须是方块 11-19 或托盘 21-26。") from exc

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise VmProtocolError("VisionMaster 固定数据响应不是 UTF-8 文本。") from exc
    if text.strip().startswith(("b'", 'b"')):
        raise VmProtocolError("VisionMaster 必须返回纯文本 #0;X;Y;RZ;，不能包含字节字面量。")

    match = _VM_TRIGGER_RESPONSE_PATTERN.fullmatch(text.strip())
    if match is None:
        raise VmProtocolError("VisionMaster 必须返回一条纯文本 #0;X;Y;RZ; 数据。")
    packet = match.group("packet")
    try:
        x, y, rz = (float(match.group(name)) for name in ("x", "y", "rz"))
    except ValueError as exc:
        raise VmProtocolError("VisionMaster 坐标 X、Y、RZ 必须是数字。") from exc
    color, kind = expected_key
    return VisionMeasurement(
        color=color,
        kind=kind,
        x=x,
        y=y,
        rz=rz,
        raw_packet=packet,
        received_at=datetime.now().astimezone(),
    )


def _number(value: float) -> str:
    return str(int(value)) if value.is_integer() else str(value)
