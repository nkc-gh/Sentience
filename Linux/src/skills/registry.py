"""
SENTIENCE Skills Manager.

Manages user-defined manual skills and agent-observed "boss skills"
(patterns learned about the user). Stored in JSON under ~/.sentience/skills/.
"""

import json
import re
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Skill:
    """A user-defined manual skill."""
    id: str = ""
    name: str = ""
    description: str = ""
    trigger: str = ""           # When/how to activate
    instructions: str = ""      # What to do
    created_at: str = ""
    is_builtin: bool = False


@dataclass
class BossSkill:
    """An agent-observed pattern about the user."""
    id: str = ""
    observation: str = ""       # What the agent noticed
    context: str = ""           # When it was observed
    confidence: float = 0.0     # How confident the agent is
    times_seen: int = 0
    first_seen: str = ""
    last_seen: str = ""


class SkillsManager:
    """Manages manual skills and boss_skills."""

    def __init__(self, skills_dir: Path):
        self.skills_dir = skills_dir
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._skills_file = self.skills_dir / "skills.json"
        self._boss_file = self.skills_dir / "boss_skills.json"
        self._builtins_marker = self.skills_dir / "builtin_skills_v1.json"
        self._skills: Dict[str, Skill] = {}
        self._boss_skills: Dict[str, BossSkill] = {}
        self._load()

    def _load(self):
        if self._skills_file.exists():
            data = json.loads(self._skills_file.read_text())
            for sid, sdata in data.items():
                self._skills[sid] = Skill(**sdata)
        if self._boss_file.exists():
            data = json.loads(self._boss_file.read_text())
            for bid, bdata in data.items():
                self._boss_skills[bid] = BossSkill(**bdata)

    def _save_skills(self):
        data = {sid: asdict(s) for sid, s in self._skills.items()}
        self._skills_file.write_text(json.dumps(data, indent=2))

    def _save_boss(self):
        data = {bid: asdict(b) for bid, b in self._boss_skills.items()}
        self._boss_file.write_text(json.dumps(data, indent=2))

    # ── Manual Skills ────────────────────────────────────

    def add_skill(self, name: str, description: str, trigger: str = "", instructions: str = "") -> Skill:
        sid = uuid.uuid4().hex[:8]
        skill = Skill(
            id=sid, name=name, description=description,
            trigger=trigger, instructions=instructions,
            created_at=datetime.now().isoformat()
        )
        self._skills[sid] = skill
        self._save_skills()
        return skill

    def remove_skill(self, skill_id: str) -> bool:
        if skill_id in self._skills:
            del self._skills[skill_id]
            self._save_skills()
            return True
        return False

    def get_skills(self) -> List[Skill]:
        return list(self._skills.values())

    def install_builtin_skills(self) -> None:
        """Install/upgrade built-in skills while preserving custom user skills."""
        if self._builtins_marker.exists():
            try:
                if json.loads(self._builtins_marker.read_text()).get("version", 0) >= 3:
                    return
            except (OSError, json.JSONDecodeError):
                pass

        from skills.builtins import BUILTIN_SKILLS

        for skill_id, skill in list(self._skills.items()):
            if skill.is_builtin or skill_id.startswith("builtin_"):
                old_path = self.skills_dir / (skill.name.casefold().replace(" ", "_") + ".md")
                old_path.unlink(missing_ok=True)
                del self._skills[skill_id]

        existing_names = {skill.name.casefold() for skill in self._skills.values()}
        for definition in BUILTIN_SKILLS:
            if definition["name"].casefold() in existing_names:
                continue
            skill = Skill(
                id=definition["id"],
                name=definition["name"],
                description=definition["description"],
                trigger=definition["trigger"],
                instructions=definition["instructions"],
                created_at=datetime.now().isoformat(),
                is_builtin=True,
            )
            self._skills[skill.id] = skill
            existing_names.add(skill.name.casefold())
            skill_filename = definition["name"].casefold().replace(" ", "_") + ".md"
            skill_path = self.skills_dir / skill_filename
            if not skill_path.exists():
                skill_path.write_text(definition["instructions"], encoding="utf-8")

        self._save_skills()
        self._builtins_marker.write_text(
            json.dumps({"version": 3, "installed_at": datetime.now().isoformat()}, indent=2),
            encoding="utf-8",
        )

    # ── Boss Skills (learned patterns) ───────────────────

    def add_boss_skill(self, observation: str, context: str = "", confidence: float = 0.5) -> BossSkill:
        bid = uuid.uuid4().hex[:8]
        now = datetime.now().isoformat()
        bs = BossSkill(
            id=bid, observation=observation, context=context,
            confidence=confidence, times_seen=1,
            first_seen=now, last_seen=now
        )
        self._boss_skills[bid] = bs
        self._save_boss()
        return bs

    def get_boss_skills(self) -> List[BossSkill]:
        return list(self._boss_skills.values())

    def learn_from_message(self, message: str) -> Optional[BossSkill]:
        """Compactly retain explicit preferences and corrections from each user turn."""
        text = " ".join(message.strip().split())
        if not text or len(text) > 12000:
            return None
        if re.search(r"(?i)(api[_ -]?key|password|secret|private key|token\s*[:=])", text):
            return None
        cue = re.search(
            r"(?i)(?:i prefer|i like|i want|from now on|always|never|don't|do not|dont|please use|please keep|make sure|remember that|no[, ]|that's wrong|that is wrong|i meant|instead[, ]|not what i asked)",
            text,
        )
        if not cue:
            return None
        sentence = re.split(r"(?<=[.!?])\s+", text[cue.start():], maxsplit=1)[0]
        observation = sentence[:220].rstrip(" ,;.")
        if len(observation) < 8:
            return None
        normalized = observation.casefold()
        for item in self._boss_skills.values():
            if item.observation.casefold() == normalized:
                item.times_seen += 1
                item.confidence = min(0.99, item.confidence + 0.08)
                item.last_seen = datetime.now().isoformat()
                self._save_boss()
                self._write_compact_profile()
                return item
        item = self.add_boss_skill(observation, context="Explicit instruction/correction", confidence=0.65)
        ranked = sorted(self._boss_skills.values(), key=lambda entry: (entry.confidence, entry.last_seen), reverse=True)
        for obsolete in ranked[32:]:
            self._boss_skills.pop(obsolete.id, None)
        self._save_boss()
        self._write_compact_profile()
        return item

    def apply_model_learning(self, updates: List[str], forget: Optional[List[str]] = None) -> None:
        """Apply compact, explicit preference updates returned by the quiet turn review."""
        forget_keys = {" ".join(str(value).split()).casefold() for value in (forget or []) if value}
        changed = False
        for item in list(self._boss_skills.values()):
            if item.observation.casefold() in forget_keys:
                self._boss_skills.pop(item.id, None)
                changed = True
        for raw in updates[:5]:
            observation = " ".join(str(raw).split()).strip(" -•\t")[:220].rstrip(" ,;.")
            if len(observation) < 8 or re.search(r"(?i)(api[_ -]?key|password|secret|private key|token\s*[:=])", observation):
                continue
            normalized = observation.casefold()
            existing = next((item for item in self._boss_skills.values() if item.observation.casefold() == normalized), None)
            if existing:
                existing.times_seen += 1
                existing.confidence = min(0.99, existing.confidence + 0.05)
                existing.last_seen = datetime.now().isoformat()
                changed = True
                continue
            self.add_boss_skill(observation, context="Quiet model review of user preference/feedback", confidence=0.7)
            changed = True
        ranked = sorted(self._boss_skills.values(), key=lambda item: (item.confidence, item.last_seen), reverse=True)
        for obsolete in ranked[32:]:
            self._boss_skills.pop(obsolete.id, None)
            changed = True
        if changed:
            self._save_boss()
        # Keep this file compact and current after every reviewed turn, even if
        # the user message did not add a durable preference.
        self._write_compact_profile()

    def get_compact_boss_profile(self, max_chars: int = 2200) -> str:
        items = sorted(self._boss_skills.values(), key=lambda entry: (entry.confidence, entry.times_seen, entry.last_seen), reverse=True)
        bullets = []
        used = 0
        for item in items[:24]:
            bullet = f"- {item.observation[:220]}"
            if used + len(bullet) > max_chars:
                break
            bullets.append(bullet)
            used += len(bullet)
        return "\n".join(bullets)

    def _write_compact_profile(self) -> None:
        profile = self.get_compact_boss_profile()
        (self.skills_dir / "boss_profile.md").write_text(
            "# Compact learned preference profile\n\n" + (profile or "No learned preferences yet.\n"),
            encoding="utf-8",
        )

    def remove_boss_skill(self, skill_id: str) -> bool:
        if skill_id in self._boss_skills:
            del self._boss_skills[skill_id]
            self._save_boss()
            return True
        return False
