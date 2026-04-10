"""
modules/__init__.py
====================
Module loader framework. Scans modules/ for module.json manifests
and loads enabled modules according to module_registry.json.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MODULES_DIR = Path(__file__).parent
_REGISTRY_PATH = _MODULES_DIR / "module_registry.json"


class ModuleLoader:
    """
    Scans modules/ subdirectories for module.json manifests.
    Respects the enable/disable flags in module_registry.json.
    """

    def __init__(self):
        self._registry: dict[str, bool] = self._load_registry()
        self._manifests: dict[str, dict[str, Any]] = {}
        self._scan()

    def _load_registry(self) -> dict[str, bool]:
        if _REGISTRY_PATH.exists():
            try:
                data = json.loads(_REGISTRY_PATH.read_text())
                return {k: bool(v) for k, v in data.items()}
            except Exception as e:
                log.warning(f"[ModuleLoader] Could not load registry: {e}")
        return {}

    def _scan(self) -> None:
        for entry in sorted(_MODULES_DIR.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "module.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
                name = manifest.get("name") or entry.name
                enabled = self._registry.get(name, manifest.get("enabled", True))
                manifest["enabled"] = enabled
                manifest["_path"] = str(entry)
                self._manifests[name] = manifest
                log.info(f"[ModuleLoader] Found module '{name}' enabled={enabled}")
            except Exception as e:
                log.warning(f"[ModuleLoader] Could not load manifest {manifest_path}: {e}")

    def get_enabled_modules(self) -> list[dict[str, Any]]:
        return [m for m in self._manifests.values() if m.get("enabled")]

    def get_manifest(self, name: str) -> dict[str, Any] | None:
        return self._manifests.get(name)

    def is_enabled(self, name: str) -> bool:
        m = self._manifests.get(name)
        return bool(m and m.get("enabled"))

    def disable_module(self, name: str) -> None:
        if name in self._manifests:
            self._manifests[name]["enabled"] = False
            self._save_registry()

    def enable_module(self, name: str) -> None:
        if name in self._manifests:
            self._manifests[name]["enabled"] = True
            self._save_registry()

    def _save_registry(self) -> None:
        data = {name: m.get("enabled", True) for name, m in self._manifests.items()}
        try:
            _REGISTRY_PATH.write_text(json.dumps(data, indent=2))
        except Exception as e:
            log.warning(f"[ModuleLoader] Could not save registry: {e}")


# Singleton loader instance
_loader: ModuleLoader | None = None


def get_loader() -> ModuleLoader:
    global _loader
    if _loader is None:
        _loader = ModuleLoader()
    return _loader
