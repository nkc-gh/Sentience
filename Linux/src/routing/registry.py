"""
SENTIENCE Provider & Model Manager.

Stores LLM provider configs (API keys, endpoints) and model
definitions in a JSON file under ~/.sentience/config/.
Includes preset model catalogs for known providers.
"""

import json
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional


# ═══════════════════════════════════════════════════════════
# No hardcoded model catalogs — all models must be fetched
# ═══════════════════════════════════════════════════════════

KNOWN_MODELS: Dict[str, List[tuple]] = {}


# ═══════════════════════════════════════════════════════════
# Task Modes & Routing Configuration
# ═══════════════════════════════════════════════════════════

TASK_MODES = ["chat", "reasoning", "code", "embedding"]

TASK_MODE_INFO = {
    "chat": {
        "name": "Chat",
        "icon": "💬",
        "desc": "Conversational assistant, dialogue, and general user queries",
    },
    "reasoning": {
        "name": "Reasoning",
        "icon": "🧠",
        "desc": "Deep logic, complex analysis, planning, and multi-step deduction",
    },
    "code": {
        "name": "Code",
        "icon": "💻",
        "desc": "Code generation, debugging, refactoring, and AST inspection",
    },
    "embedding": {
        "name": "Embedding",
        "icon": "🔍",
        "desc": "Semantic search, vector embeddings, and RAG retrieval",
    },
}


@dataclass
class LLMModel:
    """A single LLM model entry."""
    id: str = ""
    name: str = ""
    provider: str = ""
    model_id: str = ""
    is_local: bool = False
    is_default: bool = False
    context_window: Optional[int] = None


@dataclass
class TaskRoute:
    """A route configuration for a specific task mode."""
    task: str
    local_model_id: Optional[str] = None
    online_model_id: Optional[str] = None
    local_model_ids: List[str] = field(default_factory=list)
    online_model_ids: List[str] = field(default_factory=list)
    active_scope: str = "hybrid"  # "hybrid" | "local" | "online"

    def __post_init__(self):
        # Preserve existing providers.json files while moving to ordered candidate lists.
        if self.local_model_id and self.local_model_id not in self.local_model_ids:
            self.local_model_ids.insert(0, self.local_model_id)
        if self.online_model_id and self.online_model_id not in self.online_model_ids:
            self.online_model_ids.insert(0, self.online_model_id)
        self.local_model_id = self.local_model_ids[0] if self.local_model_ids else None
        self.online_model_id = self.online_model_ids[0] if self.online_model_ids else None


@dataclass
class Provider:
    """An LLM provider (cloud or local)."""
    id: str = ""
    name: str = ""
    kind: str = "cloud"
    api_key: str = ""
    base_url: str = ""
    models: List[LLMModel] = field(default_factory=list)


class ProviderManager:
    """Manages providers, models, and task routes, persisted to JSON."""

    def __init__(self, config_dir: Path):
        self.config_dir = config_dir
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.config_dir / "providers.json"
        self._providers: Dict[str, Provider] = {}
        self._routes: Dict[str, TaskRoute] = {}
        self._current_model_id: Optional[str] = None
        self._session_id: str = uuid.uuid4().hex[:12]
        self._load()

    # ── Persistence ──────────────────────────────────────

    def _load(self):
        if self._file.exists():
            data = json.loads(self._file.read_text())
            self._current_model_id = data.get("current_model_id")
            for pid, pdata in data.get("providers", {}).items():
                raw_models = pdata.pop("models", [])
                models = []
                for m in raw_models:
                    models.append(LLMModel(**m))
                self._providers[pid] = Provider(**pdata, models=models)

            raw_routes = data.get("routes", {})
            self._routes = {}
            for mode in TASK_MODES:
                if mode in raw_routes:
                    rdata = raw_routes[mode]
                    raw_scope = rdata.get("active_scope", "hybrid")
                    if raw_scope in ("local", "strict", "strict_local"):
                        clean_scope = "local"
                    elif raw_scope in ("online", "online_only", "cloud", "strict_online"):
                        clean_scope = "online"
                    else:
                        clean_scope = "hybrid"
                    self._routes[mode] = TaskRoute(
                        task=mode,
                        local_model_id=rdata.get("local_model_id"),
                        online_model_id=rdata.get("online_model_id"),
                        local_model_ids=list(rdata.get("local_model_ids") or []),
                        online_model_ids=list(rdata.get("online_model_ids") or []),
                        active_scope=clean_scope,
                    )
                else:
                    self._routes[mode] = TaskRoute(task=mode)
            valid_identifiers = {
                identifier
                for model in self.get_all_models(include_hybrid=True)
                for identifier in (model.id, model.model_id)
                if identifier
            }
            routes_changed = False
            for route in self._routes.values():
                old_local = list(route.local_model_ids)
                old_online = list(route.online_model_ids)
                route.local_model_ids = [mid for mid in old_local if mid in valid_identifiers]
                route.online_model_ids = [mid for mid in old_online if mid in valid_identifiers]
                route.local_model_id = route.local_model_ids[0] if route.local_model_ids else None
                route.online_model_id = route.online_model_ids[0] if route.online_model_ids else None
                routes_changed |= (
                    route.local_model_ids != old_local or route.online_model_ids != old_online
                )
            if routes_changed:
                self._save()
        else:
            self._routes = {mode: TaskRoute(task=mode) for mode in TASK_MODES}
            self._save()

    def _save(self):
        data = {
            "current_model_id": self._current_model_id,
            "providers": {},
            "routes": {mode: asdict(r) for mode, r in self._routes.items()},
        }
        for pid, p in self._providers.items():
            pd = asdict(p)
            data["providers"][pid] = pd
        self._file.write_text(json.dumps(data, indent=2))

    # ── Session ──────────────────────────────────────────

    @property
    def session_id(self) -> str:
        return self._session_id

    # ── Providers ────────────────────────────────────────

    def add_provider(self, name: str, kind: str, api_key: str = "", base_url: str = "") -> Provider:
        pid = uuid.uuid4().hex[:8]
        provider = Provider(id=pid, name=name, kind=kind, api_key=api_key, base_url=base_url)
        self._providers[pid] = provider
        self._save()
        return provider

    def remove_provider(self, pid: str) -> bool:
        if pid in self._providers:
            removed_model_ids = {
                identifier
                for model in self._providers[pid].models
                for identifier in (model.id, model.model_id)
                if identifier
            }
            del self._providers[pid]
            # Provider replacement is used by model discovery. Remove route
            # references to the old model records at the same time; otherwise
            # every re-discovery leaves stale IDs that appear in the routing
            # table but can never be selected by the agent.
            for route in self._routes.values():
                route.local_model_ids = [
                    model_id for model_id in route.local_model_ids
                    if model_id not in removed_model_ids
                ]
                route.online_model_ids = [
                    model_id for model_id in route.online_model_ids
                    if model_id not in removed_model_ids
                ]
                route.local_model_id = route.local_model_ids[0] if route.local_model_ids else None
                route.online_model_id = route.online_model_ids[0] if route.online_model_ids else None
            if self._current_model_id in removed_model_ids:
                remaining = self.get_all_models()
                self._current_model_id = remaining[0].id if remaining else None
            self._save()
            return True
        return False

    def get_providers(self) -> List[Provider]:
        return list(self._providers.values())

    # ── Models ───────────────────────────────────────────

    def add_model(
        self,
        provider_id: str,
        name: str,
        model_id: str,
        context_window: Optional[int] = None,
    ) -> Optional[LLMModel]:
        prov = self._providers.get(provider_id)
        if not prov:
            return None
        mid = uuid.uuid4().hex[:8]
        model = LLMModel(
            id=mid, name=name, provider=prov.name,
            model_id=model_id, is_local=(prov.kind == "local"),
            context_window=context_window if isinstance(context_window, int) and context_window > 0 else None,
        )
        prov.models.append(model)
        if self._current_model_id is None:
            self._current_model_id = mid
            model.is_default = True
        self._save()
        return model

    def get_all_models(self, include_hybrid: bool = False) -> List[LLMModel]:
        models = []
        for p in self._providers.values():
            models.extend(p.models)
        return models

    def get_current_model(self) -> Optional[LLMModel]:
        all_models = self.get_all_models()
        for m in all_models:
            if m.id == self._current_model_id or m.model_id == self._current_model_id:
                return m
        if all_models:
            self._current_model_id = all_models[0].id
            return all_models[0]
        return None

    def set_current_model(self, model_id: str) -> bool:
        all_models = self.get_all_models()
        for m in all_models:
            if m.id == model_id or m.model_id == model_id:
                for om in all_models:
                    om.is_default = False
                m.is_default = True
                self._current_model_id = m.id
                self._save()
                return True
        return False

    def remove_model(self, model_id: str) -> bool:
        for p in self._providers.values():
            for i, m in enumerate(p.models):
                if m.id == model_id:
                    p.models.pop(i)
                    if self._current_model_id == model_id:
                        rem = self.get_all_models()
                        self._current_model_id = rem[0].id if rem else None
                    self._save()
                    return True
        return False

    # ── Task Routing ──────────────────────────────────────

    def get_routes(self) -> Dict[str, TaskRoute]:
        """Return all 4 task route configurations."""
        return dict(self._routes)

    def get_route(self, task: str) -> Optional[TaskRoute]:
        """Get route configuration for a specific task mode."""
        return self._routes.get(task.lower().strip())

    def set_route(
        self,
        task: str,
        local_model_id: Optional[str] = None,
        online_model_id: Optional[str] = None,
        active_scope: Optional[str] = None,
    ) -> Optional[TaskRoute]:
        """Configure route mapping for a task mode."""
        t_key = task.lower().strip()
        if t_key not in self._routes:
            self._routes[t_key] = TaskRoute(task=t_key)
        route = self._routes[t_key]
        if local_model_id is not None:
            if local_model_id:
                if local_model_id not in route.local_model_ids:
                    route.local_model_ids.append(local_model_id)
            else:
                route.local_model_ids = []
            route.local_model_id = route.local_model_ids[0] if route.local_model_ids else None
        if online_model_id is not None:
            if online_model_id:
                if online_model_id not in route.online_model_ids:
                    route.online_model_ids.append(online_model_id)
            else:
                route.online_model_ids = []
            route.online_model_id = route.online_model_ids[0] if route.online_model_ids else None
        if active_scope is not None:
            if active_scope in ("local", "strict", "strict_local"):
                route.active_scope = "local"
            elif active_scope in ("online", "online_only", "cloud", "strict_online"):
                route.active_scope = "online"
            else:
                route.active_scope = "hybrid"
        self._save()
        return route

    def set_route_models(
        self,
        task: str,
        local_model_ids: Optional[List[str]] = None,
        online_model_ids: Optional[List[str]] = None,
    ) -> Optional[TaskRoute]:
        """Replace ordered local/online model candidates for a task."""
        t_key = task.lower().strip()
        if t_key not in self._routes:
            self._routes[t_key] = TaskRoute(task=t_key)
        route = self._routes[t_key]
        if local_model_ids is not None:
            route.local_model_ids = list(dict.fromkeys(local_model_ids))
            route.local_model_id = route.local_model_ids[0] if route.local_model_ids else None
        if online_model_ids is not None:
            route.online_model_ids = list(dict.fromkeys(online_model_ids))
            route.online_model_id = route.online_model_ids[0] if route.online_model_ids else None
        self._save()
        return route

    def remove_route(self, task: str, target: str = "all") -> bool:
        """Clear model assignment for a task mode (target: 'local', 'online', or 'all')."""
        t_key = task.lower().strip()
        if t_key not in self._routes:
            return False
        route = self._routes[t_key]
        if target == "local":
            route.local_model_ids = []
            route.local_model_id = None
        elif target == "online":
            route.online_model_ids = []
            route.online_model_id = None
        else:
            route.local_model_ids = []
            route.online_model_ids = []
            route.local_model_id = None
            route.online_model_id = None
            route.active_scope = "hybrid"
        self._save()
        return True

    def get_model_by_id(self, model_identifier: str) -> Optional[LLMModel]:
        """Find a model across all providers matching either its unique id or model_id."""
        if not model_identifier:
            return None
        for m in self.get_all_models(include_hybrid=True):
            if m.id == model_identifier or m.model_id == model_identifier or m.name == model_identifier:
                return m
        return None

    def get_local_models(self) -> List[LLMModel]:
        """Return all currently discovered local models."""
        return [m for m in self.get_all_models() if m.is_local and m.id != "hybrid-mode"]

    def get_online_models(self) -> List[LLMModel]:
        """Return all currently discovered online/cloud models."""
        return [m for m in self.get_all_models() if not m.is_local and m.id != "hybrid-mode"]

    def resolve_model_for_task(
        self,
        task_mode: str,
        require_local: bool = False,
    ) -> tuple[Optional[LLMModel], str]:
        """Resolve the model mapped to a task mode."""
        t_key = task_mode.lower().strip()
        route = self._routes.get(t_key)

        if not route:
            return None, "No models assigned to this task"
        scope = "local" if require_local else route.active_scope
        candidates = self.get_models_for_task(t_key, scope=scope)
        if candidates:
            return candidates[0], f"Assigned {('Local' if candidates[0].is_local else 'Online')} Model"
        if scope == "local":
            return None, "No local model assigned to this task"
        if scope == "online":
            return None, "No online model assigned to this task"
        return None, "No models assigned to this task"

    def get_models_for_task(self, task_mode: str, scope: Optional[str] = None) -> List[LLMModel]:
        """Return only explicitly assigned candidates permitted by the task's scope."""
        route = self._routes.get(task_mode.lower().strip())
        if not route:
            return []
        allowed_scope = scope or route.active_scope
        if allowed_scope in ("strict", "strict_local"):
            allowed_scope = "local"
        elif allowed_scope in ("online_only", "cloud", "strict_online"):
            allowed_scope = "online"
        ids: List[str] = []
        if allowed_scope in ("local", "strict", "strict_local"):
            ids = list(route.local_model_ids)
        elif allowed_scope in ("online", "online_only", "cloud", "strict_online"):
            ids = list(route.online_model_ids)
        else:
            ids = list(route.online_model_ids) + list(route.local_model_ids)
        models = []
        for model_id in ids:
            model = self.get_model_by_id(model_id)
            if model and ((allowed_scope == "local" and model.is_local) or (allowed_scope == "online" and not model.is_local) or allowed_scope == "hybrid"):
                models.append(model)
        return models

    def get_fallback_models_for_task(self, task_mode: str, current_model_id: str) -> List[LLMModel]:
        """Return ordered fallback candidates without violating explicit scope policy."""
        route = self._routes.get(task_mode.lower().strip())
        current = self.get_model_by_id(current_model_id)
        if not route or not current:
            return []
        local = [m for mid in route.local_model_ids if (m := self.get_model_by_id(mid)) and m.is_local]
        online = [m for mid in route.online_model_ids if (m := self.get_model_by_id(mid)) and not m.is_local]
        primary_scope = "local" if current.is_local else "online"
        if route.active_scope == "local":
            candidates = local
        elif route.active_scope == "online":
            candidates = online
        elif primary_scope == "local":
            candidates = local + online
        else:
            candidates = online + local
        seen = {current.id}
        result = []
        for model in candidates:
            if model.id not in seen:
                seen.add(model.id)
                result.append(model)
        return result

    @staticmethod
    def get_known_models(provider_name: str) -> List[tuple]:
        """All models are dynamically fetched from providers — no hardcoded lists."""
        return []

