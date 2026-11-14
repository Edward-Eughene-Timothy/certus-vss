"""
conversation_state.py: Lightweight structured state container for conversational turns.
Tracks active event anchoring, signal contexts, and persona state without storing
unbounded raw LLM outputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ConversationState:
    """Tracks state across natural language conversational turns."""
    active_role: str = "driver"
    active_vehicle_id: Optional[str] = None
    active_event_id: Optional[int] = None
    active_vss_path: Optional[str] = None
    last_dtc_code: Optional[str] = None
    last_intent: Optional[str] = None
    last_numbers_discussed: Dict[str, float] = field(default_factory=dict)

    def pin_event(
        self,
        event_id: int,
        vss_path: str,
        dtc_code: Optional[str],
        semantic_numbers: Optional[Dict[str, float]] = None,
    ) -> None:
        """Pins an active event only after successful evidence validation."""
        self.active_event_id = event_id
        self.active_vss_path = vss_path
        self.last_dtc_code = dtc_code
        if semantic_numbers:
            self.last_numbers_discussed.update(semantic_numbers)

    def switch_vehicle(self, new_vehicle_id: Optional[str]) -> None:
        """Switches vehicle context and invalidates event references from the previous vehicle."""
        if self.active_vehicle_id != new_vehicle_id:
            self.active_vehicle_id = new_vehicle_id
            self.active_event_id = None
            self.active_vss_path = None
            self.last_dtc_code = None
            self.last_numbers_discussed.clear()

    def set_role(self, role: str) -> None:
        """Updates active role while strictly preserving pinned diagnostic evidence."""
        if role not in {"driver", "technician", "fleet_operator"}:
            raise ValueError(f"Unsupported role: {role}")
        self.active_role = role

    def reset_event(self) -> None:
        """Clears pinned event when explicit new inquiry occurs without event context."""
        self.active_event_id = None
        self.active_vss_path = None
        self.last_dtc_code = None
        self.last_numbers_discussed.clear()
