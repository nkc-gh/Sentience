"""
SENTIENCE Storage Initialization.

Creates the XDG-compliant directory structure on first run
and ensures all directories exist on subsequent runs.
"""

import os
import stat
from config import SentienceConfig


def initialize_storage(config: SentienceConfig) -> None:
    """Create the full XDG directory structure.

    Safe to call repeatedly — directories that already exist are left
    untouched.  Runtime directory gets restrictive permissions (0700).
    """
    directories = [
        # Config
        config.config_dir,
        config.policies_dir,
        config.skills_dir,
        config.hooks_dir,
        # Data
        config.data_dir,
        config.file_history_dir,
        config.repos_dir,
        config.memory_dir,
        config.vector_index_dir,
        # State
        config.state_dir,
        config.log_dir,
        config.audit_dir,
        config.migrations_dir,
        config.task_state_dir,
        # Cache
        config.cache_dir,
    ]
    for d in directories:
        d.mkdir(parents=True, exist_ok=True)

    # Runtime directory — restrictive permissions (mode 0700)
    runtime = config.runtime_dir
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(runtime), stat.S_IRWXU)
    except OSError:
        pass
