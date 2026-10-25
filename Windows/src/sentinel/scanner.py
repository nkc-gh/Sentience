"""Content-aware local scanner used by Sentinel.

Filename hints are deliberately only one signal. Content patterns, entropy,
structured assignments and lightweight intent cues are checked on every file.
The scanner never returns secret values in its findings.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("credential_assignment", re.compile(r"(?im)\b(password|passwd|pwd|secret|token|api[_-]?key|client[_-]?secret)\b['\"]?\s*[:=]\s*['\"]?[^\s,'\"}]{6,}")),
    ("cloud_key", re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}\b")),
    ("bearer_token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~-]{20,}")),
    ("connection_string", re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb|redis)://[^\s]+")),
)
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)),
    ("phone", re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")),
    ("ip_address", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("credit_card_candidate", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
)
_NAME_HINT = re.compile(r"(?i)\b(?:name|manager|owner|contact|recipient)\s*[:=]\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3})")
_SENSITIVE_NAME_HINTS = (".env", "password", "credential", "secret", "token", "private", "key")


@dataclass(frozen=True)
class Finding:
    kind: str
    category: str
    line: int | None = None
    redacted_preview: str = ""
    action: str = "inspect"


@dataclass
class ScanReport:
    path: str
    findings: list[Finding] = field(default_factory=list)
    readable: bool = True
    error: str = ""

    @property
    def has_secrets(self) -> bool:
        return any(f.kind == "secret" for f in self.findings)

    @property
    def has_pii(self) -> bool:
        return any(f.kind == "pii" for f in self.findings)

    @property
    def has_sensitive_findings(self) -> bool:
        return self.has_secrets or self.has_pii

    @property
    def cloud_action(self) -> str:
        if self.has_secrets:
            return "local_only"
        if self.has_pii:
            return "tokenize"
        return "allow"


def _luhn(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _redacted(value: str) -> str:
    if len(value) <= 4:
        return "[REDACTED]"
    return value[:2] + "…" + value[-2:]


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


class ContentScanner:
    """Deterministic baseline scanner with extension points for recognizers."""

    def __init__(self, extra_secret_patterns: Iterable[tuple[str, str]] = ()):
        self.secret_patterns = list(_SECRET_PATTERNS)
        self.secret_patterns.extend((name, re.compile(pattern, re.I | re.M)) for name, pattern in extra_secret_patterns)

    def scan_text(self, path: str, text: str) -> ScanReport:
        report = ScanReport(path)
        name = Path(path).name.casefold()
        if any(hint in name for hint in _SENSITIVE_NAME_HINTS):
            report.findings.append(Finding("secret", "sensitive_filename", action="local_only"))

        lines = text.splitlines()
        for category, pattern in self.secret_patterns:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                report.findings.append(Finding("secret", category, line, _redacted(match.group(0)), "local_only"))

        for category, pattern in _PII_PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0)
                if category == "credit_card_candidate" and not _luhn(value):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                report.findings.append(Finding("pii", category, line, _redacted(value), "tokenize"))

        for match in _NAME_HINT.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            report.findings.append(Finding("pii", "person_name", line, _redacted(match.group(1)), "tokenize"))

        # High-entropy assignment values are a useful generic signal for
        # unknown tokens, but do not classify ordinary long prose as a secret.
        for match in re.finditer(r"(?im)^\s*[A-Z][A-Z0-9_-]{2,}\s*=\s*([^\s#]+)", text):
            value = match.group(1).strip("'\"")
            if len(value) >= 16 and _entropy(value) >= 3.2:
                line = text.count("\n", 0, match.start()) + 1
                report.findings.append(Finding("secret", "high_entropy_assignment", line, _redacted(value), "local_only"))

        # Deduplicate findings created by filename and content rules.
        report.findings = list(dict.fromkeys(report.findings))
        return report

    def scan_file(self, path: str | Path, max_bytes: int = 5_000_000) -> ScanReport:
        path = Path(path).expanduser()
        try:
            if not path.is_file():
                return ScanReport(str(path), readable=False, error="not a regular file")
            if path.stat().st_size > max_bytes:
                return ScanReport(str(path), readable=False, error="file exceeds scanner size limit")
            return self.scan_text(str(path), path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            return ScanReport(str(path), readable=False, error=str(exc))


def tokenize_text(text: str, report: ScanReport) -> tuple[str, dict[str, str]]:
    """Replace detected PII locally; secrets are never tokenized for cloud use."""
    replacements: list[tuple[str, str]] = []
    counter: dict[str, int] = {}
    for finding in report.findings:
        if finding.kind != "pii":
            continue
        # Re-scan the relevant category to obtain exact values without ever
        # storing secret findings. Names are intentionally handled by the
        # explicit name recognizer only.
        patterns = dict(_PII_PATTERNS)
        pattern = patterns.get(finding.category) or (_NAME_HINT if finding.category == "person_name" else None)
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            value = match.group(0) if finding.category != "person_name" else match.group(1)
            key = finding.category.upper()
            counter[key] = counter.get(key, 0) + 1
            replacements.append((value, f"{{{{{key}_{counter[key]}}}}}"))
    for value, token in sorted(set(replacements), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(value, token)
    return text, {token: value for value, token in replacements}


def sanitize_for_cloud(path: str, text: str, scanner: ContentScanner | None = None) -> tuple[str, ScanReport, dict[str, str]]:
    """Create a cloud-safe copy while retaining the original locally.

    Secret-bearing content is redacted, never tokenized. PII is replaced with
    task-local opaque placeholders. A sensitive-looking filename is treated as
    local-only even when its contents happen not to match a known pattern.
    """
    scanner = scanner or ContentScanner()
    report = scanner.scan_text(path, text)
    if any(hint in Path(path).name.casefold() for hint in _SENSITIVE_NAME_HINTS):
        return "[LOCAL-ONLY FILE CONTENT OMITTED]", report, {}
    sanitized = text
    for category, pattern in scanner.secret_patterns:
        sanitized = pattern.sub("[REDACTED_SECRET]", sanitized)
    # Generic uppercase assignments catch unfamiliar environment variables
    # such as DB_PASSWORD even when their value is not a known key prefix.
    assignment = re.compile(r"(?im)^(\s*[A-Z][A-Z0-9_-]{2,}\s*=\s*)([^\s#]+)")
    def redact_assignment(match: re.Match[str]) -> str:
        value = match.group(2).strip("'\"")
        return match.group(1) + ("[REDACTED_SECRET]" if len(value) >= 12 and _entropy(value) >= 2.8 else match.group(2))
    sanitized = assignment.sub(redact_assignment, sanitized)
    # Redacted secret-bearing text can still contain PII; tokenize that PII
    # after secret replacement so no personal identifier is left behind.
    sanitized, mapping = tokenize_text(sanitized, report)
    return sanitized, report, mapping
