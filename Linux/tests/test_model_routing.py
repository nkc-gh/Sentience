"""
Unit tests for Sentience Model Routing & Task Mode Configuration.
"""

from unittest.mock import MagicMock
from pathlib import Path

from routing.registry import (
    ProviderManager,
    TASK_MODES,
    TASK_MODE_INFO,
)
from storage.database import Database
from storage.models import TrackedFolder, PrivacyLabel
from cli.commands.archive import format_access_badges
from cli.commands.routing import render_routes_table
from cli.commands.model import discover_models_for_provider


def test_task_routing_storage_and_lifecycle(tmp_path):
    """Verify route creation, persistence across ProviderManager instances, and route removal."""
    pm = ProviderManager(tmp_path)

    # Verify all 4 predefined task modes are initialized
    routes = pm.get_routes()
    for mode in TASK_MODES:
        assert mode in routes
        assert routes[mode].task == mode
        assert routes[mode].active_scope == "hybrid"

    # Add local provider and models
    p_local = pm.add_provider("Ollama", "local", base_url="http://localhost:11434")
    m_local1 = pm.add_model(p_local.id, "Llama 3.1 8B", "llama3.1:8b")
    m_local2 = pm.add_model(p_local.id, "Qwen 2.5 Coder 32B", "qwen2.5-coder:32b")

    # Add cloud provider and models
    p_cloud = pm.add_provider("Anthropic", "cloud", api_key="sk-ant-test")
    m_cloud1 = pm.add_model(p_cloud.id, "Claude 3.5 Sonnet", "claude-3-5-sonnet-20241022")
    m_cloud2 = pm.add_model(p_cloud.id, "Claude Opus", "claude-opus-5-5-20260922")

    # Configure routes
    pm.set_route("code", local_model_id=m_local2.model_id, online_model_id=m_cloud1.model_id, active_scope="hybrid")
    pm.set_route("reasoning", online_model_id=m_cloud2.model_id, active_scope="hybrid")
    pm.set_route("chat", local_model_id=m_local1.model_id, active_scope="local")

    # Test persistence
    pm2 = ProviderManager(tmp_path)
    r_code = pm2.get_route("code")
    assert r_code.local_model_id == "qwen2.5-coder:32b"
    assert r_code.online_model_id == "claude-3-5-sonnet-20241022"
    assert r_code.active_scope == "hybrid"

    r_chat = pm2.get_route("chat")
    assert r_chat.local_model_id == "llama3.1:8b"
    assert r_chat.active_scope == "local"

    # Test removal
    assert pm2.remove_route("code", target="local") is True
    assert pm2.get_route("code").local_model_id is None
    assert pm2.get_route("code").online_model_id == "claude-3-5-sonnet-20241022"

    assert pm2.remove_route("code", target="all") is True
    assert pm2.get_route("code").online_model_id is None
    assert pm2.get_route("code").active_scope == "hybrid"


def test_resolve_model_with_file_privacy_constraints(tmp_path):
    """Verify routing settings resolve assigned local and online models."""
    pm = ProviderManager(tmp_path)

    p_loc = pm.add_provider("Ollama", "local", base_url="http://localhost:11434")
    m_loc = pm.add_model(p_loc.id, "Qwen Coder", "qwen2.5-coder:32b")

    p_cloud = pm.add_provider("OpenAI", "cloud", api_key="sk-test")
    m_cloud = pm.add_model(p_cloud.id, "GPT-4o", "gpt-4o")

    # Route: code has both local and online
    pm.set_route("code", local_model_id=m_loc.model_id, online_model_id=m_cloud.model_id, active_scope="hybrid")

    # Standard (Hybrid allowed): should resolve to online model
    resolved, reason = pm.resolve_model_for_task("code", require_local=False)
    assert resolved is not None
    assert resolved.model_id == "gpt-4o"
    assert "Assigned Online Model" in reason

    # Strict Local enforced: must resolve to local model
    resolved_strict, reason_strict = pm.resolve_model_for_task("code", require_local=True)
    assert resolved_strict is not None
    assert resolved_strict.model_id == "qwen2.5-coder:32b"
    assert resolved_strict.is_local is True
    assert "Assigned Local Model" in reason_strict


def test_multiple_ordered_route_models_and_scoped_fallback(tmp_path):
    pm = ProviderManager(tmp_path)
    local_provider = pm.add_provider("Ollama", "local")
    local_primary = pm.add_model(local_provider.id, "Local primary", "local-a")
    local_backup = pm.add_model(local_provider.id, "Local backup", "local-b")
    online_provider = pm.add_provider("OpenAI", "cloud", api_key="test")
    online_primary = pm.add_model(online_provider.id, "Online primary", "online-a")
    online_backup = pm.add_model(online_provider.id, "Online backup", "online-b")

    pm.set_route_models("chat", [local_primary.id, local_backup.id], [online_primary.id, online_backup.id])
    pm.set_route("chat", active_scope="hybrid")
    assert [m.id for m in pm.get_fallback_models_for_task("chat", online_primary.id)] == [
        online_backup.id, local_primary.id, local_backup.id,
    ]
    assert [m.id for m in pm.get_fallback_models_for_task("chat", local_primary.id)] == [
        local_backup.id, online_primary.id, online_backup.id,
    ]

    pm.set_route("chat", active_scope="online")
    assert [m.id for m in pm.get_fallback_models_for_task("chat", online_primary.id)] == [online_backup.id]
    assert all(not model.is_local for model in pm.get_models_for_task("chat"))

    pm.set_route("chat", active_scope="local")
    assert [m.id for m in pm.get_fallback_models_for_task("chat", local_primary.id)] == [local_backup.id]
    assert all(model.is_local for model in pm.get_models_for_task("chat"))


def test_resolver_does_not_pick_unassigned_global_model(tmp_path):
    pm = ProviderManager(tmp_path)
    local = pm.add_provider("Ollama", "local")
    pm.add_model(local.id, "Unassigned local", "unassigned")
    online = pm.add_provider("OpenAI", "cloud", api_key="test")
    pm.add_model(online.id, "Unassigned online", "unassigned-online")
    pm.set_route("chat", online_model_id="removed-online-id", active_scope="online")
    model, _ = pm.resolve_model_for_task("chat")
    assert model is None


def test_tracked_folder_model_scope_in_db(tmp_path):
    """Verify database schema migration and persistence of model_scope (hybrid vs local)."""
    db = Database(tmp_path / "sentience.db")

    folder1 = TrackedFolder(
        path=str(tmp_path / "project_public"),
        label=PrivacyLabel.PUBLIC,
        model_scope="hybrid",
    )
    saved1 = db.add_folder(folder1)
    assert saved1.id is not None
    assert saved1.model_scope == "hybrid"

    folder2 = TrackedFolder(
        path=str(tmp_path / "private_vault"),
        label=PrivacyLabel.RESTRICTED,
        model_scope="local",
    )
    saved2 = db.add_folder(folder2)
    assert saved2.model_scope == "local"

    # Reload from DB
    loaded2 = db.get_folder(saved2.id)
    assert loaded2 is not None
    assert loaded2.model_scope == "local"

    # Update permissions and scope
    db.update_folder_permissions(
        saved2.id,
        track=True,
        read=True,
        edit=False,
        index=True,
        history=True,
        model_scope="hybrid",
    )
    updated2 = db.get_folder(saved2.id)
    assert updated2.model_scope == "hybrid"

    db.close()


def test_archive_badges_without_model_scope():
    """Verify archive access badges focus on filesystem permissions without model scope badges."""
    f = TrackedFolder(path="/tmp/a", track_allowed=True, read_allowed=True, edit_allowed=False)
    badges = format_access_badges(f)
    assert "Track" in badges
    assert "Read" in badges
    assert "Edit" in badges
    assert "Hybrid" not in badges
    assert "Local Only" not in badges


def test_routing_table_rendering(tmp_path):
    """Verify the Rich table for model routing renders cleanly for all 4 task modes."""
    pm = ProviderManager(tmp_path)
    mock_shell = MagicMock()
    mock_shell.providers = pm

    table = render_routes_table(mock_shell)
    assert table is not None
    assert len(table.rows) == 4  # Chat, Reasoning, Code, Embedding


def test_local_provider_discovery_fallbacks():
    """Verify model discovery queries endpoint dynamically without hardcoded model lists."""
    from unittest.mock import patch
    import io

    # Mock dynamic Ollama response
    mock_payload = b'{"models": [{"name": "qwen2.5-coder:7b"}, {"name": "llama3.2:3b"}]}'
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = io.BytesIO(mock_payload)
        mock_urlopen.return_value.__enter__.return_value = mock_resp
        models_local = discover_models_for_provider("Ollama", base_url="http://localhost:11434")
        assert len(models_local) == 2
        assert "qwen2.5-coder:7b" in models_local
        assert "llama3.2:3b" in models_local

    # Offline/unreachable server returns empty list (zero hardcoded fallback models)
    models_unreachable = discover_models_for_provider("LMStudio", base_url="http://localhost:9999")
    assert models_unreachable == []
