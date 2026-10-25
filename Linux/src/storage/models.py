"""
SENTIENCE Data Models.

Pydantic models that define the canonical shapes for every piece of
data that flows through the system.  Aligned with spec §10.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════
# Enumerations
# ═══════════════════════════════════════════════════════════


class PrivacyLabel(str, Enum):
    """Privacy classification for a tracked folder."""
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"
    PRIVATE = "PRIVATE"


class TrackingStatus(str, Enum):
    """Whether a tracked folder is actively being monitored."""
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"


class FileEventType(str, Enum):
    """The kind of filesystem operation that was observed."""
    CREATED = "CREATED"
    MODIFIED = "MODIFIED"
    DELETED = "DELETED"
    RENAMED = "RENAMED"
    MOVED = "MOVED"


class PrivacyMode(str, Enum):
    """Privacy operating mode per spec §6.8."""
    STRICT_LOCAL = "strict_local"
    PRIVATE_CLOUD = "private_cloud"
    CUSTOM = "custom"


class TrackingState(str, Enum):
    """Global tracking state."""
    ON = "on"
    PAUSED = "paused"
    OFF = "off"


class EvidenceLevel(str, Enum):
    """How claims about activity are labeled per spec §5.6."""
    OBSERVED = "observed"
    CALCULATED = "calculated"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class RouteMode(str, Enum):
    """Model preference mode per spec §7.4."""
    PINNED = "pinned"
    PREFERRED = "preferred"
    AUTOMATIC = "automatic"


class ActionRiskTier(str, Enum):
    """Connector action risk tier per spec §8.6."""
    READ = "read"
    PREPARE = "prepare"
    MODIFY = "modify"
    FINANCIAL = "financial"
    GRANT_ACCESS = "grant_access"


# ═══════════════════════════════════════════════════════════
# Core Data Models — spec §10
# ═══════════════════════════════════════════════════════════


class TrackedFolder(BaseModel):
    """A user-selected directory that SENTIENCE is allowed to monitor (TrackedRoot)."""
    id: Optional[int] = None
    path: str
    label: PrivacyLabel = PrivacyLabel.PUBLIC
    status: TrackingStatus = TrackingStatus.ACTIVE
    read_allowed: bool = True
    edit_allowed: bool = False
    index_allowed: bool = True
    track_allowed: bool = True
    history_allowed: bool = True
    model_scope: str = "hybrid"  # "hybrid" (local + online AI) or "local" (strict local AI only)
    exclusions: str = ""  # Comma-separated patterns
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class FileEvent(BaseModel):
    """A single filesystem change event inside a tracked folder."""
    id: Optional[int] = None
    folder_id: int
    event_type: FileEventType
    file_path: str
    old_path: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    git_commit: Optional[str] = None
    lines_added: int = 0
    lines_removed: int = 0
    is_directory: bool = False
    source: str = "filesystem_watcher"
    session_id: Optional[str] = None


class WindowSession(BaseModel):
    """An aggregated period of time that a single application window was active."""
    id: Optional[int] = None
    app_name: str
    window_title: str
    start_time: datetime
    end_time: Optional[datetime] = None
    duration_seconds: Optional[float] = None
    time_str: Optional[str] = None
    is_idle: bool = False
    capture_completeness: str = "full"  # full | partial | unknown

    def model_post_init(self, __context: Any) -> None:
        if not self.time_str and self.start_time:
            self.time_str = self.start_time.strftime("%I:%M %p").lstrip("0")


class ShellCommand(BaseModel):
    """A single command captured from the user's shell."""
    id: Optional[int] = None
    shell_type: str
    command: str
    working_directory: str
    exit_code: Optional[int] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    duration_seconds: Optional[float] = None


class GitSnapshot(BaseModel):
    """A shadow-repo commit capturing a point-in-time snapshot."""
    id: Optional[int] = None
    folder_id: int
    commit_hash: str
    timestamp: datetime = Field(default_factory=datetime.now)
    message: str = ""
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


class Chat(BaseModel):
    """A conversation session per spec §10."""
    id: Optional[int] = None
    name: str = "New Chat"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    archived: bool = False
    project: Optional[str] = None
    default_route: Optional[str] = None
    models_used: List[str] = Field(default_factory=list)
    workspace_path: Optional[str] = None
    workspace_read: bool = False
    workspace_write: bool = False
    active_skill_ids: List[str] = Field(default_factory=list)
    enabled_modes: List[str] = Field(default_factory=lambda: ["chat"])
    active_mode: Optional[str] = None
    active_model_id: Optional[str] = None
    enabled_local_sources: List[str] = Field(default_factory=list)


class Message(BaseModel):
    """A single message in a chat per spec §10."""
    id: Optional[int] = None
    chat_id: int
    role: str  # user | assistant | system
    body: str
    timestamp: datetime = Field(default_factory=datetime.now)
    provider: Optional[str] = None
    model_id: Optional[str] = None
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    context_limit: Optional[int] = None
    runtime_context: Optional[int] = None
    local_sources_used: List[str] = Field(default_factory=list)


class MemoryItem(BaseModel):
    """Learned information about the user (boss) per spec §10."""
    id: Optional[str] = None
    content: str
    source: str = "observation"
    timestamp: datetime = Field(default_factory=datetime.now)
    is_explicit: bool = False
    confidence: float = 0.5
    times_seen: int = 1
    edit_state: str = "active"  # active | deleted


class ModelRecord(BaseModel):
    """A registered model per spec §10."""
    id: Optional[str] = None
    name: str
    provider: str
    model_id: str
    is_local: bool = False
    capabilities: List[str] = Field(default_factory=list)
    modalities: List[str] = Field(default_factory=lambda: ["text"])
    is_default: bool = False
    context_window: Optional[int] = None


class ModelProvider(BaseModel):
    """A registered provider per spec §10."""
    id: Optional[str] = None
    name: str
    kind: str = "cloud"  # cloud | local
    api_key: str = ""
    base_url: str = ""
    discovery_supported: bool = False
    health: str = "unknown"
    models: List[ModelRecord] = Field(default_factory=list)


class RoutePolicy(BaseModel):
    """Task→model mapping per spec §10."""
    task_category: str
    mode: RouteMode = RouteMode.AUTOMATIC
    model_id: Optional[str] = None
    fallbacks: List[str] = Field(default_factory=list)


class ConnectorAccount(BaseModel):
    """A connected external service per spec §10."""
    id: Optional[str] = None
    name: str
    provider: str
    status: str = "disconnected"
    scopes: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)


class AgentTask(BaseModel):
    """A running or completed agent task per spec §10."""
    id: Optional[str] = None
    description: str = ""
    state: str = "pending"  # pending | running | completed | cancelled | failed
    current_step: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None


class AuditEvent(BaseModel):
    """Minimal audit record per spec §10."""
    operation_type: str
    target: str = ""
    policy_decision: str = ""
    reason_code: str = ""
    timestamp: datetime = Field(default_factory=datetime.now)
    outcome: str = ""
