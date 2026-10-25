"""Small, provider-independent Sentinel policy layer.

Sentinel owns the decision about where work may run. Provider adapters and
document writers remain outside this module so adding a model runtime cannot
silently change privacy behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable


class SentinelMode(str, Enum):
    LOCAL = "local"
    HYBRID = "hybrid"
    CLOUD = "cloud"


@dataclass(frozen=True)
class RouteDecision:
    mode: SentinelMode
    local_required: bool
    cloud_allowed: bool
    privacy_checks_enabled: bool
    routing_enabled: bool
    reason: str


@dataclass(frozen=True)
class FileRoute:
    path: str
    local_only: bool
    cloud_allowed: bool
    reason: str


@dataclass(frozen=True)
class SentinelPolicy:
    mode: SentinelMode = SentinelMode.HYBRID

    @classmethod
    def from_value(cls, value: str | SentinelMode | None) -> "SentinelPolicy":
        if isinstance(value, SentinelMode):
            return cls(value)
        try:
            return cls(SentinelMode(str(value or SentinelMode.HYBRID.value).lower()))
        except ValueError:
            return cls(SentinelMode.HYBRID)

    def task_decision(self) -> RouteDecision:
        if self.mode is SentinelMode.LOCAL:
            return RouteDecision(self.mode, True, False, True, True, "local mode")
        if self.mode is SentinelMode.CLOUD:
            return RouteDecision(self.mode, False, True, False, False, "cloud mode explicitly bypasses Sentinel checks")
        return RouteDecision(self.mode, False, True, True, True, "hybrid mode scans locally before cloud use")

    def route_files(self, reports: Iterable[object]) -> list[FileRoute]:
        """Route scanner reports without removing requested files from output."""
        result = []
        for report in reports:
            path = str(getattr(report, "path", ""))
            is_sensitive = bool(getattr(report, "has_sensitive_findings", False))
            if self.mode is SentinelMode.LOCAL:
                result.append(FileRoute(path, True, False, "local mode"))
            elif self.mode is SentinelMode.CLOUD:
                result.append(FileRoute(path, False, True, "cloud mode bypasses Sentinel checks"))
            elif is_sensitive:
                result.append(FileRoute(path, True, False, "sensitive content stays local in hybrid mode"))
            else:
                result.append(FileRoute(path, False, True, "safe content may use an eligible cloud route"))
        return result


def load_policy(config, default: SentinelMode = SentinelMode.HYBRID) -> SentinelPolicy:
    """Load the persisted mode while remaining compatible with old configs."""
    value = config.get_setting("sentinel_mode", default.value)
    return SentinelPolicy.from_value(value)


def save_policy(config, mode: SentinelMode | str) -> SentinelPolicy:
    policy = SentinelPolicy.from_value(mode)
    config.set_setting("sentinel_mode", policy.mode.value)
    return policy
