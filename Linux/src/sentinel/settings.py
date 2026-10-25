"""Rendering helpers for the deliberately small Sentinel settings UI."""

from sentinel.policy import SentinelMode, SentinelPolicy


def mode_description(mode: SentinelMode) -> str:
    return {
        SentinelMode.LOCAL: "Everything stays on this computer.",
        SentinelMode.HYBRID: "Sentinel protects sensitive data and routes safe work.",
        SentinelMode.CLOUD: "Direct cloud processing; Sentinel privacy and routing are bypassed.",
    }[mode]


def settings_text(policy: SentinelPolicy) -> str:
    lines = [
        "Sentinel Settings",
        "",
        f"Current mode: {policy.mode.value.upper()}",
        "",
        "1. Local",
        "   Everything stays on this computer.",
        "",
        "2. Hybrid",
        "   Sentinel protects sensitive data and routes safe work.",
        "",
        "3. Cloud",
        "   Direct cloud processing; Sentinel privacy and routing are bypassed.",
    ]
    return "\n".join(lines)
