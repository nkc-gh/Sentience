import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class ConnectorManager:
    """Manages 3rd-party integrations (OAuth credentials, endpoints)."""
    def __init__(self, config_dir: str):
        self.config_dir = Path(config_dir) / "connectors"
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.config_dir / "connectors.json"
        
        self.connectors: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        if not self.config_path.exists():
            return {}
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            logger.error("Failed to decode connectors.json, starting fresh.")
            return {}

    def _save(self):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.connectors, f, indent=2)
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass

    def add_connector(self, name: str, config: Dict[str, Any]):
        self.connectors[name] = config
        self._save()

    def remove_connector(self, name: str) -> bool:
        if name in self.connectors:
            del self.connectors[name]
            self._save()
            return True
        return False

    def get_connector(self, name: str) -> Optional[Dict[str, Any]]:
        return self.connectors.get(name)

    def list_connectors(self) -> List[str]:
        return list(self.connectors.keys())

    @staticmethod
    def _keyring():
        try:
            import keyring
        except ImportError as exc:
            raise RuntimeError("Secure credential storage is unavailable; install the keyring dependency.") from exc
        if getattr(keyring.get_keyring(), "priority", 0) <= 0:
            raise RuntimeError("No OS credential vault is available. Set up Secret Service before adding a connector.")
        return keyring

    def save_secret(self, name: str, value: str) -> None:
        self._keyring().set_password("sentience-connectors", name, value)

    def get_secret(self, name: str) -> Optional[str]:
        return self._keyring().get_password("sentience-connectors", name)

    def remove_secret(self, name: str) -> None:
        try:
            self._keyring().delete_password("sentience-connectors", name)
        except Exception:
            pass

    def clear_credentials(self) -> None:
        for name in (
            "google-client", "google-token", "amadeus-client-id",
            "amadeus-client-secret", "amadeus-access-token",
            "railcore-api-key", "navitia-api-key",
        ):
            self.remove_secret(name)

