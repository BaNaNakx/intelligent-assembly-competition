'One shared clock for the competition display and log export views.'

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic


@dataclass(slots=True)
class CompetitionClock:
    started_at: float
    duration_seconds: int = 300
    paused_at: float | None = None
    paused_seconds: float = 0.0

    @classmethod
    def start(cls, duration_seconds: int = 300) -> "CompetitionClock":
        return cls(monotonic(), duration_seconds)

    @property
    def is_paused(self) -> bool:
        return self.paused_at is not None

    def pause(self, now: float | None = None) -> None:
        if self.paused_at is None:
            self.paused_at = monotonic() if now is None else now

    def resume(self, now: float | None = None) -> None:
        if self.paused_at is None:
            return
        current = monotonic() if now is None else now
        self.paused_seconds += max(0.0, current - self.paused_at)
        self.paused_at = None

    def _active_now(self, now: float | None) -> float:
        if self.paused_at is not None:
            return self.paused_at
        return monotonic() if now is None else now

    def elapsed_seconds(self, now: float | None = None) -> int:
        current = self._active_now(now)
        return max(0, int(current - self.started_at - self.paused_seconds))

    def remaining_seconds(self, now: float | None = None) -> int:
        return max(0, self.duration_seconds - self.elapsed_seconds(now))
