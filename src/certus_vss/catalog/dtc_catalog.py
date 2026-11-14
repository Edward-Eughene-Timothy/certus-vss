"""
dtc_catalog.py: Reference catalog for Diagnostic Trouble Codes (DTC).
All entries are strictly provisional reference data subject to OEM verification.
"""
from __future__ import annotations

from typing import Dict, Optional

PROVISIONAL_DTC_CATALOG: Dict[str, Dict[str, str]] = {
    "P0A7F": {
        "title": "Hybrid/EV Traction Battery Pack Deterioration (Provisional Reference)",
        "description": "Provisional reference indicates potential cell module degradation or localized thermal imbalance under load. Unconfirmed against OEM specifications.",
        "service_procedure": "Provisional Procedure TSB-2026-BATT-04: Inspect battery coolant circuit manifold and log cell module resistance. Requires OEM manual verification.",
    },
    "U0100": {
        "title": "Lost Communication with ECM/PCM (Provisional Reference)",
        "description": "Provisional reference indicates communication interruption on propulsion CAN bus. Unconfirmed against OEM specifications.",
        "service_procedure": "Provisional Procedure TSB-2026-CAN-09: Inspect CAN bus termination resistance and physical harness connector integrity. Requires OEM manual verification.",
    },
}


def get_dtc_reference(dtc_code: Optional[str]) -> Optional[Dict[str, str]]:
    """Return provisional reference metadata for DTC code, or None if absent/unrecognized."""
    if not dtc_code or not isinstance(dtc_code, str):
        return None
    return PROVISIONAL_DTC_CATALOG.get(dtc_code.strip())
