'AUBO ES5 tool-output adapter for the competition suction cup.'

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from .robot_parameters import RobotParameterStore


class ToolDigitalOutputPort(Protocol):
    def set_tool_digital_output(self, index: int, value: bool) -> int: ...


@dataclass(frozen=True, slots=True)
class ToolSuctionConfig:
    output_index: int = 1
    active_value: bool = True
    suction_settle_s: float = 0.20
    release_settle_s: float = 0.20
    blow_output_index: int = 0

    def __post_init__(self) -> None:
        if not 0 <= self.output_index <= 3 or not 0 <= self.blow_output_index <= 3:
            raise ValueError("吸盘工具数字输出索引必须在0到3之间。")
        if self.output_index == self.blow_output_index:
            raise ValueError("吸气和吹气必须使用不同的工具数字输出。")
        if self.suction_settle_s < 0 or self.release_settle_s < 0:
            raise ValueError("吸盘稳定等待时间不能为负数。")


class ArcsToolSuctionIo:
    def __init__(
        self,
        client: ToolDigitalOutputPort,
        config: ToolSuctionConfig = ToolSuctionConfig(),
        *,
        sleep: Callable[[float], None] = time.sleep,
        parameter_store: RobotParameterStore | None = None,
    ) -> None:
        self._client = client
        self._config = config
        self._sleep = sleep
        self._parameter_store = parameter_store

    def set_suction(self, enabled: bool) -> None:
        config = self._current_config()
        active = config.active_value
        inactive = not active
        if enabled:
            self._client.set_tool_digital_output(config.blow_output_index, inactive)
            self._client.set_tool_digital_output(config.output_index, active)
            if config.suction_settle_s:
                self._sleep(config.suction_settle_s)
            return
        self._client.set_tool_digital_output(config.output_index, inactive)
        self._client.set_tool_digital_output(config.blow_output_index, active)
        if config.release_settle_s:
            self._sleep(config.release_settle_s)
        self._client.set_tool_digital_output(config.blow_output_index, inactive)

    def _current_config(self) -> ToolSuctionConfig:
        if self._parameter_store is None:
            return self._config
        parameters = self._parameter_store.snapshot()
        return ToolSuctionConfig(
            output_index=parameters.tool_do_index,
            active_value=parameters.tool_do_active_high,
            suction_settle_s=parameters.suction_settle_s,
            release_settle_s=parameters.release_settle_s,
            blow_output_index=parameters.tool_blow_do_index,
        )
