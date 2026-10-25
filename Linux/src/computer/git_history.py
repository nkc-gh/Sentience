"""
Shadow Git Repository Manager.

For every tracked folder SENTIENCE maintains a *shadow* Git repository
that stores periodic snapshots of the folder's content.  This avoids
polluting the user's own Git history while providing full version
tracking and diff generation.

Shadow repos live under ``~/.sentience/memory/repositories/<name>_<hash>/``.
"""

import hashlib
import logging
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from config import SentienceConfig

logger = logging.getLogger(__name__)

# Directories / patterns excluded from syncing into the shadow repo.
_SYNC_EXCLUDES = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    ".sentience",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
}


class ShadowGitManager:
    """Manages shadow Git repositories for tracked folders."""

    def __init__(self, config: SentienceConfig):
        self.config = config
        self.repos_dir = config.repos_dir

    # ── Path helpers ─────────────────────────────────────

    def _repo_path(self, folder_path: str) -> Path:
        name = Path(folder_path).name
        path_hash = hashlib.md5(folder_path.encode()).hexdigest()[:8]
        return self.repos_dir / f"{name}_{path_hash}"

    # ── Initialise ───────────────────────────────────────

    def init_repo(self, folder_path: str) -> Path:
        """Initialise (or verify) a shadow Git repo for *folder_path*."""
        repo = self._repo_path(folder_path)
        repo.mkdir(parents=True, exist_ok=True)

        if not (repo / ".git").exists():
            self._git(repo, ["init"])
            self._git(repo, ["config", "user.email", "sentience@local"])
            self._git(repo, ["config", "user.name", "SENTIENCE"])
            # Seed .gitignore so common binaries are excluded.
            (repo / ".gitignore").write_text(
                "*.pyc\n__pycache__/\nnode_modules/\n*.so\n*.o\n*.a\n"
                "*.dll\n*.exe\n*.bin\n*.dat\n*.db\n*.sqlite\n*.sqlite3\n"
            )
            self._git(repo, ["add", ".gitignore"])
            self._git(repo, ["commit", "-m", "Initial SENTIENCE tracking"])
            logger.info("Initialised shadow repo: %s", repo)

        return repo

    # ── Snapshot ─────────────────────────────────────────

    def snapshot(
        self, folder_path: str, message: Optional[str] = None
    ) -> Optional[Dict]:
        """Sync the tracked folder into its shadow repo and commit.

        Returns a dict with commit metadata, or ``None`` if nothing
        changed since the last snapshot.
        """
        repo = self._repo_path(folder_path)
        if not (repo / ".git").exists():
            self.init_repo(folder_path)

        self._sync_files(folder_path, repo)
        self._git(repo, ["add", "-A"])

        # Anything staged?
        status = self._git(repo, ["status", "--porcelain"]).strip()
        if not status:
            return None

        diff_stat = self._git(repo, ["diff", "--cached", "--stat"])

        ts = datetime.now().isoformat()
        name = Path(folder_path).name
        msg = message or f"Snapshot of {name} at {ts}"
        self._git(repo, ["commit", "-m", msg])

        commit_hash = self._git(repo, ["rev-parse", "HEAD"]).strip()
        stats = self._parse_diff_stat(diff_stat)

        return {
            "commit_hash": commit_hash,
            "timestamp": ts,
            "message": msg,
            "files_changed": stats["files_changed"],
            "lines_added": stats["insertions"],
            "lines_removed": stats["deletions"],
        }

    # ── Diff / history ───────────────────────────────────

    def diff(
        self,
        folder_path: str,
        commit1: Optional[str] = None,
        commit2: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> str:
        """Return a unified diff between two commits (default: last change)."""
        repo = self._repo_path(folder_path)
        if not (repo / ".git").exists():
            return ""

        rel_target: Optional[str] = None
        if file_path:
            rel_target = Path(file_path).name if Path(folder_path).is_file() else file_path
        elif Path(folder_path).is_file():
            rel_target = Path(folder_path).name

        try:
            if commit1 and commit2:
                cmd = ["diff", commit1, commit2]
                if rel_target:
                    cmd += ["--", rel_target]
                return self._git(repo, cmd)
            elif commit1:
                # View diff introduced by commit1
                cmd = ["show", "--pretty=", commit1]
                if rel_target:
                    cmd += ["--", rel_target]
                res = self._git(repo, cmd)
                if res.strip():
                    return res
                cmd = ["diff", f"{commit1}~1", commit1]
                if rel_target:
                    cmd += ["--", rel_target]
                return self._git(repo, cmd)
            else:
                cmd = ["diff", "HEAD~1", "HEAD"]
                if rel_target:
                    cmd += ["--", rel_target]
                res = self._git(repo, cmd)
                if res.strip():
                    return res
                res = self._git(repo, ["show", "--pretty=", "HEAD"] + (["--", rel_target] if rel_target else []))
                if res.strip():
                    return res
                return self._git(repo, ["diff", "HEAD"] + (["--", rel_target] if rel_target else []))
        except Exception:
            return ""

    def file_history(
        self,
        folder_path: str,
        file_path: Optional[str] = None,
        limit: Optional[int] = 50,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> List[Dict]:
        """Commit log for a single file (follows renames)."""
        repo = self._repo_path(folder_path)
        if not (repo / ".git").exists():
            return []

        target_file = file_path
        if Path(folder_path).is_file():
            target_file = Path(folder_path).name
        elif not target_file:
            return self.log(folder_path, limit=limit)

        try:
            command = ["log"]
            if limit is not None:
                command.append(f"-{limit}")
            if since is not None:
                command.append(f"--since={since.strftime('%Y-%m-%d %H:%M:%S')}")
            if until is not None:
                command.append(f"--until={until.strftime('%Y-%m-%d %H:%M:%S')}")
            command.extend(["--pretty=format:%H|%aI|%s", "--follow", "--", target_file])
            raw = self._git(repo, command)
        except Exception:
            return []

        entries: List[Dict] = []
        for line in raw.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                entries.append(
                    {"commit": parts[0], "timestamp": parts[1], "message": parts[2]}
                )
        return entries

    def log(self, folder_path: str, limit: Optional[int] = 20) -> List[Dict]:
        """Recent commits for the entire shadow repo."""
        repo = self._repo_path(folder_path)
        command = ["log"]
        if limit is not None:
            command.append(f"-{limit}")
        command.append("--pretty=format:%H|%aI|%s")
        try:
            raw = self._git(repo, command)
        except Exception:
            return []

        entries: List[Dict] = []
        for line in raw.strip().splitlines():
            parts = line.split("|", 2)
            if len(parts) == 3:
                entries.append(
                    {"commit": parts[0], "timestamp": parts[1], "message": parts[2]}
                )
        return entries

    # ── Internal helpers ─────────────────────────────────

    def _sync_files(self, source_dir: str, repo_path: Path) -> None:
        """Mirror *source_dir* → *repo_path* (excluding noise)."""
        source = Path(source_dir)
        if source.is_file():
            dest = repo_path / source.name
            try:
                shutil.copy2(source, dest)
            except Exception as e:
                logger.warning("Failed to copy %s to %s: %s", source, dest, e)
            return

        if not source.exists():
            dest = repo_path / source.name
            if dest.exists():
                try:
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                except Exception:
                    pass
            return

        try:
            exclude_args: List[str] = []
            for ex in _SYNC_EXCLUDES:
                exclude_args += ["--exclude", ex]
            src_str = str(source).rstrip("/") + "/"
            subprocess.run(
                ["rsync", "-a", "--delete"] + exclude_args
                + [src_str, f"{repo_path}/"],
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            # rsync failed or not installed — fall back to manual copy.
            self._manual_sync(source_dir, repo_path)
        except subprocess.TimeoutExpired:
            logger.warning("rsync timed out for %s", source_dir)

    def _manual_sync(self, source_dir: str, repo_path: Path) -> None:
        """Pure-Python fallback when rsync is unavailable."""
        source = Path(source_dir)
        if source.is_file():
            dest = repo_path / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, dest)
            except Exception:
                pass
            return

        if not source.exists():
            dest = repo_path / source.name
            if dest.exists():
                try:
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                except Exception:
                    pass
            return

        repo_files: set[Path] = set()
        for f in repo_path.rglob("*"):
            if f.is_file() and not any(ex in f.parts for ex in _SYNC_EXCLUDES):
                repo_files.add(f.relative_to(repo_path))

        source_files: set[Path] = set()
        for f in source.rglob("*"):
            if f.is_file() and not any(ex in f.parts for ex in _SYNC_EXCLUDES):
                rel = f.relative_to(source)
                source_files.add(rel)
                dest = repo_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    if not dest.exists() or f.stat().st_mtime > dest.stat().st_mtime:
                        shutil.copy2(f, dest)
                except (OSError, PermissionError):
                    pass

        for rel in repo_files - source_files:
            target = repo_path / rel
            if target.exists():
                target.unlink()

    def _git(self, repo: Path, args: List[str]) -> str:
        result = subprocess.run(
            ["git"] + args,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 and "nothing to commit" not in result.stdout:
            logger.debug(
                "git %s → rc=%d  stderr=%s",
                " ".join(args),
                result.returncode,
                result.stderr.strip(),
            )
        return result.stdout

    @staticmethod
    def _parse_diff_stat(stat_output: str) -> Dict[str, int]:
        files_changed = insertions = deletions = 0
        for line in stat_output.strip().splitlines():
            if "file" in line and "changed" in line:
                for part in line.strip().split(","):
                    part = part.strip()
                    try:
                        n = int(part.split()[0])
                    except (ValueError, IndexError):
                        continue
                    if "file" in part:
                        files_changed = n
                    elif "insertion" in part:
                        insertions = n
                    elif "deletion" in part:
                        deletions = n
        return {
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
        }
