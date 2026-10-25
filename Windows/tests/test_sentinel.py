from pathlib import Path

from config import SentienceConfig
from sentinel.policy import SentinelMode, SentinelPolicy, load_policy, save_policy
from sentinel.scanner import ContentScanner, tokenize_text
from cli.chat_runtime import _text_tool_calls


def test_scanner_detects_content_not_only_sensitive_filenames():
    report = ContentScanner().scan_text(
        "notes.txt",
        "API_KEY=sk-abcdefghijklmnopqrstuvwxyz\nContact: person@example.com\n",
    )
    assert report.has_secrets
    assert report.has_pii
    assert report.cloud_action == "local_only"


def test_scanner_detects_sensitive_content_without_exposing_values():
    report = ContentScanner().scan_text("ordinary.txt", "password = 'super-secret-value'")
    assert report.has_secrets
    assert all("super-secret-value" not in finding.redacted_preview for finding in report.findings)


def test_sanitizer_catches_json_credentials_and_unknown_env_assignments():
    source = '{"password":"correct-horse-battery-staple"}\nDB_PASSWORD=very-secret-password\n'
    sanitized, report, _ = __import__("sentinel.scanner", fromlist=["sanitize_for_cloud"]).sanitize_for_cloud(
        "diff.txt", source
    )
    assert report.has_secrets
    assert "correct-horse-battery-staple" not in sanitized
    assert "very-secret-password" not in sanitized


def test_pii_is_tokenized_but_secrets_are_not():
    scanner = ContentScanner()
    report = scanner.scan_text("message.txt", "Email Rohit Sharma at rohit@example.com")
    sanitized, mapping = tokenize_text("Email Rohit Sharma at rohit@example.com", report)
    assert "rohit@example.com" not in sanitized
    assert "{{EMAIL_" in sanitized
    assert mapping

    secret_report = scanner.scan_text("message.txt", "TOKEN=abc1234567890xyz")
    original = "TOKEN=abc1234567890xyz"
    assert tokenize_text(original, secret_report) == (original, {})


def test_modes_route_sensitive_files_without_dropping_them():
    scanner = ContentScanner()
    reports = [
        scanner.scan_text("src/app.py", "print('ok')"),
        scanner.scan_text(".env", "PASSWORD=abc123456789"),
    ]
    hybrid = SentinelPolicy(SentinelMode.HYBRID).route_files(reports)
    assert [item.path for item in hybrid] == ["src/app.py", ".env"]
    assert hybrid[0].cloud_allowed is True
    assert hybrid[1].local_only is True
    assert SentinelPolicy(SentinelMode.LOCAL).task_decision().cloud_allowed is False
    assert SentinelPolicy(SentinelMode.CLOUD).task_decision().privacy_checks_enabled is False


def test_sentinel_mode_persists(tmp_path: Path):
    config = SentienceConfig(config_dir=tmp_path / "config")
    save_policy(config, SentinelMode.LOCAL)
    assert load_policy(config).mode is SentinelMode.LOCAL


def test_text_tool_calls_normalize_local_runtime_json_output():
    tools = [{"type": "function", "function": {"name": "read_file"}},
             {"type": "function", "function": {"name": "list_files"}}]
    assert _text_tool_calls('```json {"name":"read_file","arguments":{"path":".env"}}```', tools) == [
        ("read_file", {"path": ".env"})
    ]
    assert _text_tool_calls('["list_files", {"path":""}]', tools) == [
        ("list_files", {"path": ""})
    ]
    assert _text_tool_calls('{"function":"read_file","arguments":{"path":".env"}}', tools) == [
        ("read_file", {"path": ".env"})
    ]
    assert _text_tool_calls('{"name":"delete_file","arguments":{"path":"x"}}', tools) == []
