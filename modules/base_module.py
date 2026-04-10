"""
modules/base_module.py
=======================
Abstract base class for all DemonPulse pipeline modules.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseModule(ABC):
    """
    Abstract base for all pipeline modules.

    Each module:
    - Has a unique name and type
    - Declares input_requirements and output_keys
    - Can be enabled/disabled without breaking the system
    - Processes a DogsRacePacket dict and returns updated fields
    """

    module_name: str = ""
    module_type: str = ""  # capture | analysis | simulation | results | learning | ui
    enabled: bool = True
    version: str = "1.0.0"
    input_requirements: list[str] = []
    output_keys: list[str] = []

    @abstractmethod
    def process(self, packet: dict[str, Any]) -> dict[str, Any]:
        """
        Process a race packet and return a dict of output fields to merge into the packet.
        Must not raise — return empty dict on failure.
        """
        ...

    def can_process(self, packet: dict[str, Any]) -> bool:
        """Check that all input_requirements are present and non-empty in the packet."""
        for key in self.input_requirements:
            val = packet.get(key)
            if val is None or val == {} or val == []:
                return False
        return True
