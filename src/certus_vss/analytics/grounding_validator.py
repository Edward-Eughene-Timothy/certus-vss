"""
grounding_validator.py: Final Phase 4.2C deterministic output grounding,
signal-unit parity, provenance attribution, DTC catalog verification,
range calculation verification, and domain safety validator.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from certus_vss.catalog.dtc_catalog import PROVISIONAL_DTC_CATALOG
from certus_vss.catalog.signals import CANONICAL_VSS_SIGNALS, CANONICAL_VSS_UNITS

# 1. Recognized incompatible physical units
RECOGNIZED_INCOMPATIBLE_UNITS = [
    r"\b(fahrenheit|deg\s*f|°\s*f)\b",
    r"\b(kelvin|deg\s*k|°\s*k)\b",
    r"\b(mph|miles?\s*per\s*hour)\b",
    r"\b(psi|bar|kpa)\b",
    r"\b(rpm|revolutions\s*per\s*minute)\b",
    r"\b(liters?|gallons?|fl\s*oz)\b",
    r"\b(knots?|parsecs?)\b",
]

# 2. Explicit unknown/unsupported physical units to reject when following numbers
UNSUPPORTED_UNIT_TOKENS = {
    "volts", "volt", "v", "kv", "mv",
    "quarts", "quart", "qt", "pints", "pint", "cups",
    "joules", "joule", "j", "kj", "mj", "wh", "kwh",
    "newtons", "newton", "nm", "lb", "lbs", "pounds", "kg", "grams", "g",
    "meters", "meter", "miles", "mile", "yards", "yard", "feet", "foot", "ft", "inches", "inch",
    "ohms", "ohm", "farads", "farad", "henrys", "henry", "tesla", "gauss",
    "pascal", "pascals", "atm", "torr", "mmhg",
    "calories", "calorie", "btu", "horsepower", "hp",
}

# 3. Prescriptive ICE maintenance directives
PRESCRIPTIVE_ICE_PATTERNS = [
    r"\b(check|inspect|change|replace|top\s*up|fill|flush|service)\s+.*?\b(engine\s*oil|motor\s*oil|oil\s*filter|spark\s*plugs?|fuel\s*filter|fuel\s*pump|fuel\s*injector|gas\s*tank|transmission\s*fluid|exhaust\s*system|catalytic\s*converter)\b",
    r"\b(engine\s*oil|motor\s*oil|oil\s*filter|spark\s*plugs?|fuel\s*filter|fuel\s*pump|fuel\s*injector|gas\s*tank|transmission\s*fluid|catalytic\s*converter)\s+.*?\b(needs\s+to\s+be|should\s+be|must\s+be)\s+.*?\b(inspected|checked|replaced|serviced)\b",
]

# 4. Unsupported causal, component destruction, and fire risk assertions
UNSUPPORTED_CAUSAL_AND_HAZARD_CLAIMS = [
    r"\b(battery(\s*pack)?\s+(is|has)\s+(destroyed|melted|failed\s+completely|dead))\b",
    r"\b(catastrophic\s+cell\s+(destruction|meltdown|failure))\b",
    r"\b(thermal\s+runaway(\s+confirmed|\s+has\s+occurred|\s+is\s+underway)?)\b",
    r"\b(fire\s+hazard|risk\s+of\s+fire|imminent\s+fire|explosion\s+risk)\b",
    r"\b(confirms?\s+(cell|module)\s+(degradation|failure|damage|defect))\b",
    r"\b(proves?\s+(internal\s+resistance\s+failure|cell\s+damage|component\s+failure))\b",
    r"\b(is\s+defective|hardware\s+failure\s+confirmed)\b",
]

# 5. False claims of manufacturer certification
FALSE_OEM_AUTHORITY_PATTERNS = [
    r"\b(oem-verified|oem\s+verified|manufacturer\s+confirmed|certified\s+tsb\s+bulletin|official\s+oem\s+procedure|factory\s+approved\s+repair)\b"
]

# 6. Permitted structural integers
PERMITTED_STRUCTURAL_NUMBERS = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 24, 60, 100, 330}

# 7. Signal keyword affinity mapping
SIGNAL_AFFINITY_KEYWORDS = {
    "Vehicle.Speed": [r"\bspeed\b", r"\bvelocity\b"],
    "Vehicle.Powertrain.TractionBattery.Temperature": [
        r"\bbattery\s*temp",
        r"\btraction\s*battery\s*temp",
        r"\bbattery\s*thermal\b",
        r"\bbattery\s*heat\b",
        r"\bpack\s*temp",
    ],
    "Vehicle.Powertrain.TractionBattery.Current": [
        r"\b(?:battery\s*|discharge\s*|electrical\s*)?current\b",
        r"\bamperes?\b",
        r"\bamps?\b",
    ],
    "Vehicle.Powertrain.TractionBattery.StateOfCharge": [
        r"\bstate\s*of\s*charge\b",
        r"\bsoc\b",
        r"\bcharge\s*level\b",
        r"\bbattery\s*level\b",
        r"\bcharge\s*left\b",
        r"\bremaining\s*charge\b",
    ],
    "Vehicle.Cabin.HVAC.PowerDraw": [
        r"\bhvac\b",
        r"\bclimate\b",
        r"\bair\s*condition(?:ing)?\b",
        r"\bheater\b",
        r"\bpower\s*draw\b",
        r"\bhvac\s*power\b",
    ],
    "Vehicle.Powertrain.ElectricMotor.CoolantTemperature": [
        r"\bmotor\s*coolant\b",
        r"\bmotor\s*temp",
        r"\bcoolant\s*temp",
    ],
}

# 8. Plausible EV & ICE fabricated DTC symptom patterns not grounded in PROVISIONAL_DTC_CATALOG
FABRICATED_DTC_SYMPTOM_PATTERNS = [
    r"\b(turbocharger|boost\s+failure|transmission\s+slipping|exhaust\s+leak|spark\s+failure|fuel\s+pump)\b",
    r"\b(inverter\s+igbt\s+short|stator\s+winding\s+open|dc-dc\s+converter\s+burnout|contactor\s+welded\s+shut|onboard\s+charger\s+diode\s+failure|resolver\s+sensor\s+fault)\b",
]


@dataclass
class GroundedBenchmark:
    signal: str
    value: float
    unit: str
    category: str


def _is_valid_number(val: Any) -> bool:
    """Returns True only if val is a finite int/float and NOT a boolean."""
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return False
    return not (math.isnan(val) or math.isinf(val))


def check_context_integrity(context: Dict[str, Any]) -> Optional[str]:
    """
    Validates evidence payload data types, canonical units, timestamps, and aggregate ordering.
    Rejects booleans, numeric strings, NaNs, infinities, and invalid signals/units.
    """
    snap = context.get("active_fault_snapshot", {})
    sig = snap.get("trigger_signal")
    if sig is not None:
        if not isinstance(sig, str) or sig not in CANONICAL_VSS_SIGNALS:
            return f"Context integrity error: unknown canonical signal '{sig}'."

    trig_m = snap.get("trigger_measurement")
    if trig_m:
        if "value" not in trig_m or not _is_valid_number(trig_m["value"]):
            return "Context integrity error: trigger_value must be a finite numeric float/int."
        t_unit = trig_m.get("unit")
        if not t_unit or (sig and t_unit != CANONICAL_VSS_UNITS.get(sig)):
            return f"Context integrity error: trigger unit '{t_unit}' invalid for signal '{sig}'."

    base = snap.get("pre_fault_24h_baseline", {})
    if base:
        for k in ("average", "min", "max"):
            if k in base and base[k] is not None and not _is_valid_number(base[k]):
                return f"Context integrity error: baseline '{k}' is non-numeric or non-finite."
        b_avg = base.get("average")
        b_min = base.get("min")
        b_max = base.get("max")
        if b_avg is not None and b_min is not None and b_max is not None:
            if not (b_min <= b_avg <= b_max):
                return f"Context integrity error: baseline ordering invalid (min {b_min} <= avg {b_avg} <= max {b_max} violated)."
        b_unit = base.get("unit")
        if b_avg is not None and (not b_unit or (sig and b_unit != CANONICAL_VSS_UNITS.get(sig))):
            return f"Context integrity error: baseline unit '{b_unit}' mismatch for '{sig}'."

    rollup = context.get("long_term_historical_rollup", {})
    avail = context.get("availability_status", {})
    if avail.get("historical_rollup_status") == "AVAILABLE" and rollup.get("status") == "SUCCESS":
        r_sig = rollup.get("vss_path") or (sig if "window" in rollup else None)
        if not r_sig or r_sig not in CANONICAL_VSS_SIGNALS:
            return f"Context integrity error: rollup missing explicit canonical vss_path (got '{r_sig}')."

        r_unit = rollup.get("unit")
        expected_u = CANONICAL_VSS_UNITS.get(r_sig)
        if not r_unit or r_unit != expected_u:
            return f"Context integrity error: rollup unit '{r_unit}' mismatch for '{r_sig}' (expected '{expected_u}')."

        aggs = rollup.get("aggregates", rollup)
        for k in ("average", "min", "max"):
            if k in aggs and aggs[k] is not None and not _is_valid_number(aggs[k]):
                return f"Context integrity error: rollup '{k}' is non-numeric or non-finite."
        r_avg = aggs.get("average")
        r_min = aggs.get("min")
        r_max = aggs.get("max")
        if r_avg is not None and r_min is not None and r_max is not None:
            if not (r_min <= r_avg <= r_max):
                return f"Context integrity error: rollup ordering invalid (min {r_min} <= avg {r_avg} <= max {r_max} violated)."

        start_s = rollup.get("start_epoch_s")
        end_s = rollup.get("end_epoch_s")
        win_m = rollup.get("window_minutes")
        if start_s is not None and end_s is not None:
            if not _is_valid_number(start_s) or not _is_valid_number(end_s) or start_s > end_s:
                return f"Context integrity error: invalid rollup timestamps ({start_s} to {end_s})."
            if win_m is not None:
                if not _is_valid_number(win_m) or win_m <= 0:
                    return f"Context integrity error: invalid rollup window_minutes ({win_m})."
                max_possible_mins = ((end_s - start_s) / 60.0) + 1.0
                if win_m > max_possible_mins:
                    return f"Context integrity error: window_minutes ({win_m}) exceeds elapsed span ({max_possible_mins})."

    if "soc_val" in context and context["soc_val"] is not None:
        soc_v = context["soc_val"]
        if not _is_valid_number(soc_v) or soc_v < 0.0 or soc_v > 100.0:
            return f"Context integrity error: invalid soc_val '{soc_v}'."
        if "soc_unit" in context and context["soc_unit"] != "percent":
            return f"Context integrity error: invalid soc_unit '{context['soc_unit']}'."
        if "soc_signal" in context and context["soc_signal"] != "Vehicle.Powertrain.TractionBattery.StateOfCharge":
            return f"Context integrity error: invalid soc_signal '{context['soc_signal']}'."

    return None


def extract_factual_measurements(text: str) -> List[Tuple[float, str]]:
    """
    Extracts numerical values paired with physical units or percentages.
    Uses exact normalized unit matching to avoid substring collision.
    """
    # Strip DTC and TSB identifiers before extracting measurements so e.g. 'P0A' is not parsed as '0 amperes'
    cleaned = re.sub(r"\b[PUBC][0-9A-F]{4}\b", "", text)
    cleaned = re.sub(r"\bTSB-[0-9A-Za-z_-]+\b", "", cleaned, flags=re.IGNORECASE)

    pattern = r"(?<![A-Za-z0-9_])(-?\d+(?:\.\d+)?)\s*(%|percent|°\s*[cC]|(?:degrees?\s+)?celsius|degrees?\s+[cC]|km/h|km|watts?|w|amperes?|amps?|a)(?!\w)"
    matches = []
    for m in re.finditer(pattern, cleaned, flags=re.IGNORECASE):
        val_str, raw_unit = m.group(1), m.group(2).lower()
        val = float(val_str)
        clean_u = re.sub(r"\s+", "", raw_unit)
        if clean_u in ("°c", "celsius") or "celsius" in clean_u or clean_u.endswith("c"):
            unit = "celsius"
        elif clean_u in ("%", "percent"):
            unit = "percent"
        elif clean_u == "km/h":
            unit = "km/h"
        elif clean_u == "km":
            unit = "km"
        elif clean_u in ("w", "watt", "watts"):
            unit = "watts"
        elif clean_u in ("a", "amp", "amps", "ampere", "amperes"):
            unit = "amperes"
        else:
            unit = clean_u
        matches.append((val, unit))
    return matches


def detect_unknown_or_incompatible_units(text: str) -> Optional[str]:
    """
    Scans for numbers followed by unrecognized or incompatible physical unit tokens.
    Strips DTC/TSB identifiers first and distinguishes ordinary English prose from invalid units.
    """
    cleaned = re.sub(r"\b[PUBC][0-9A-F]{4}\b", "", text)
    cleaned = re.sub(r"\bTSB-[0-9A-Za-z_-]+\b", "", cleaned, flags=re.IGNORECASE)
    lower = cleaned.lower()

    for pat in RECOGNIZED_INCOMPATIBLE_UNITS:
        if re.search(pat, lower):
            return "Unsupported or incompatible physical unit detected."

    # Check for numbers followed by known unsupported unit tokens
    pattern = r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?\s*([A-Za-z°%][A-Za-z0-9°/%_^-]*)"
    for m in re.finditer(pattern, cleaned):
        raw_token = m.group(1).lower()
        clean = re.sub(r"[.,;:!?()\"']", "", raw_token).strip()
        if clean in UNSUPPORTED_UNIT_TOKENS:
            return f"Unknown or unsupported unit token '{clean}' detected following measurement."
    return None


def extract_unvalidated_numbers(text: str, context: Dict[str, Any]) -> List[float]:
    """
    Extracts raw numbers that are not part of recognized structural or metadata syntax.
    """
    cleaned = text
    cleaned = re.sub(r"\b[PUBC][0-9A-F]{4}\b", "", cleaned)
    cleaned = re.sub(r"\bTSB-[0-9A-Za-z_-]+\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b24[- ](?:hours?|hrs?|h)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b6[- ]signal\b", "", cleaned, flags=re.IGNORECASE)

    # Strip event ID references like Event #10 or #20
    cleaned = re.sub(r"(?:event\s*#?|#)\s*\d+\b", "", cleaned, flags=re.IGNORECASE)

    snap = context.get("active_fault_snapshot", {})
    rollup = context.get("long_term_historical_rollup", {})
    for ep in ("start_epoch_s", "end_epoch_s"):
        if rollup.get(ep):
            cleaned = re.sub(rf"\b{rollup[ep]}\b", "", cleaned)
    if snap.get("event_timestamp_utc_s"):
        cleaned = re.sub(rf"\b{snap['event_timestamp_utc_s']}\b", "", cleaned)

    base_m = snap.get("pre_fault_24h_baseline", {})
    for bep in ("window_start_epoch_s", "window_end_epoch_s"):
        if base_m.get(bep):
            cleaned = re.sub(rf"\b{base_m[bep]}\b", "", cleaned)

    # Strip epoch timestamps (9-11 digit Unix seconds) in provenance strings
    cleaned = re.sub(r"\b\d{9,11}\b", "", cleaned)
    # Strip minute counts in window descriptions like ", 2 minutes)" or "over 43210 recorded minutes"
    cleaned = re.sub(r"\b\d+\s*(?:recorded\s+)?(?:minutes?|mins?)\b", "", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\b\d+(?:\.\d+)?\s*ms\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(?:^|\n)\s*\d+[\.\)]", "", cleaned)
    cleaned = re.sub(r"\bstep\s*\d+\b", "", cleaned, flags=re.IGNORECASE)

    raw_nums = []
    for token in re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?\b", cleaned):
        try:
            raw_nums.append(float(token))
        except ValueError:
            continue
    return raw_nums


def _check_provenance_attribution(
    lower_text: str,
    val: float,
    expected_category: str,
) -> Optional[str]:
    """
    Verifies that a numerical measurement is not attributed to an incorrect
    temporal category across flexible phrasing and alternative sentence structures.
    """
    v_str = re.escape(str(val))

    labels_trigger = [
        r"event\s+trigger(?:\s+measurement)?",
        r"trigger(?:\s+measurement|\s+value|\s+reading|\s+spike)?",
        r"measured\s+at\s+event",
        r"reading\s+at\s+trigger",
        r"trigger\s+was",
    ]
    labels_24h_base = [
        r"24[- ]hour\s+(?:pre[- ]fault\s+)?baseline(?:\s+average)?",
        r"24h\s+(?:pre[- ]fault\s+)?baseline(?:\s+average)?",
        r"pre[- ]fault\s+24h\s+baseline(?:\s+average)?",
        r"pre[- ]fault\s+baseline(?:\s+average)?",
        r"baseline\s+average",
        r"24[- ]hour\s+average",
        r"24h\s+average",
    ]
    labels_long_term = [
        r"long[- ]term\s+recorded\s+database\s+average",
        r"long[- ]term\s+database\s+average",
        r"recorded\s+database\s+average",
        r"long[- ]term\s+average",
        r"historical\s+average",
        r"database\s+average",
        r"long[- ]term\s+recorded\s+average",
    ]

    def has_link(patterns: List[str]) -> bool:
        for p in patterns:
            if re.search(rf"\b(?:{p})\s*(?:is|was|of|at|reached|recorded\s+at)?\s*[:\s]*{v_str}\b", lower_text):
                return True
            if re.search(rf"\b{v_str}\s*(?:celsius|km/h|watts?|amperes?|%|percent)?\s*(?:as|was|is|representing)?\s*(?:the)?\s*(?:{p})\b", lower_text):
                return True
        return False

    if expected_category == "event_trigger":
        if has_link(labels_24h_base):
            return f"Semantic provenance error: Event trigger value {val} misattributed as 24-hour baseline average."
        if has_link(labels_long_term):
            return f"Semantic provenance error: Event trigger value {val} misattributed as long-term average."

    elif expected_category == "pre_fault_24h_average":
        if has_link(labels_trigger):
            return f"Semantic provenance error: Baseline average {val} misattributed as event trigger measurement."
        if has_link(labels_long_term):
            return f"Semantic provenance error: 24-hour baseline average {val} misattributed as long-term average."

    elif expected_category == "long_term_average":
        if has_link(labels_trigger):
            return f"Semantic provenance error: Long-term average {val} misattributed as event trigger measurement."
        if has_link(labels_24h_base):
            return f"Semantic provenance error: Long-term average {val} misattributed as 24-hour baseline average."

    return None


def validate_output_grounding(
    text: str,
    context: Dict[str, Any],
) -> Tuple[bool, Optional[str]]:
    """
    Strictly validates complete assistant output against verified factual context.
    Enforces unit-to-signal parity, semantic provenance integrity, DTC catalog
    boundaries, EV architecture limits, and non-catastrophic causal restraint.
    """
    if not text or not text.strip():
        return False, "Response text is empty."

    c_err = check_context_integrity(context)
    if c_err:
        return False, c_err

    lower_text = text.lower()

    # 1. Incompatible / Unknown Physical Units Check
    unit_err = detect_unknown_or_incompatible_units(text)
    if unit_err:
        return False, unit_err

    # 2. Prescriptive ICE Maintenance Directives Check
    for pat in PRESCRIPTIVE_ICE_PATTERNS:
        if re.search(pat, lower_text):
            return False, "Unsupported prescriptive ICE maintenance recommendation detected."

    # 3. Unsupported Causal Assertions and Hazard Claims
    for pat in UNSUPPORTED_CAUSAL_AND_HAZARD_CLAIMS:
        if re.search(pat, lower_text):
            return False, "Unsupported causal assertion, catastrophic diagnostic claim, or fire hazard detected."

    # 4. False Claims of Technical/OEM Authority
    for pat in FALSE_OEM_AUTHORITY_PATTERNS:
        if re.search(pat, lower_text):
            return False, "False technical authority claim: provisional procedure cannot be claimed as OEM-verified."

    # 5. DTC Reference Validation & Local Catalog Grounding
    dtc_pattern = r"\b([PUBC][0-9A-F]{4})\b"
    mentioned_dtcs = set(re.findall(dtc_pattern, text.upper()))
    snap = context.get("active_fault_snapshot", {})
    expected_dtc = snap.get("dtc_code")

    if not expected_dtc and mentioned_dtcs:
        return False, f"Unexpected DTC reference '{next(iter(mentioned_dtcs))}' when active event has no assigned DTC."

    for dtc in mentioned_dtcs:
        if expected_dtc and dtc != expected_dtc:
            return False, f"Unsupported DTC '{dtc}' mentioned (expected active fault: '{expected_dtc}')."

        if dtc in PROVISIONAL_DTC_CATALOG:
            cat_entry = PROVISIONAL_DTC_CATALOG[dtc]
            if re.search(r"\b(confirmed\s+repair|guaranteed\s+fix|certified\s+oem)\b", lower_text):
                return False, f"Provisional DTC '{dtc}' mislabeled as confirmed repair."
            for fab_pat in FABRICATED_DTC_SYMPTOM_PATTERNS:
                if re.search(fab_pat, lower_text):
                    return False, f"DTC '{dtc}' has fabricated symptom claim inconsistent with catalog."
            m_title = re.search(r"•\s*title:\s*(.+)", lower_text)
            if m_title and cat_entry["title"].lower() not in m_title.group(1).strip():
                return False, f"DTC '{dtc}' title does not match provisional catalog entry."
            m_proc = re.search(r"•\s*procedure\s+reference:\s*(.+)", lower_text)
            if m_proc and cat_entry["service_procedure"].lower() not in m_proc.group(1).strip():
                return False, f"DTC '{dtc}' procedure does not match provisional catalog entry."
        else:
            invented_symptom_patterns = [
                r"\b(failure\s+detected|malfunction\s+confirmed|cell\s+degradation|circuit\s+fault|sensor\s+failure)\b",
                r"\b(service\s+procedure|repair\s+steps?|replacement\s+required)\b",
            ] + FABRICATED_DTC_SYMPTOM_PATTERNS
            for isp in invented_symptom_patterns:
                if re.search(isp, lower_text):
                    return False, f"Unknown DTC '{dtc}' has no catalog entry; invented diagnostic claim detected."

    # 6. Build Precise Grounded Benchmarks (Signal-Level Parity)
    benchmarks: List[GroundedBenchmark] = []
    target_signal = snap.get("trigger_signal")
    canonical_unit = CANONICAL_VSS_UNITS.get(target_signal) if target_signal else None

    trig_m = snap.get("trigger_measurement")
    if trig_m and "value" in trig_m:
        val = round(float(trig_m["value"]), 2)
        unit = trig_m.get("unit") or canonical_unit
        if target_signal and unit:
            benchmarks.append(GroundedBenchmark(target_signal, val, unit, "event_trigger"))

    base_m = snap.get("pre_fault_24h_baseline", {})
    if base_m.get("average") is not None:
        val = round(float(base_m["average"]), 2)
        unit = base_m.get("unit") or canonical_unit
        if target_signal and unit:
            benchmarks.append(GroundedBenchmark(target_signal, val, unit, "pre_fault_24h_average"))
            if "min" in base_m and base_m["min"] is not None:
                benchmarks.append(GroundedBenchmark(target_signal, round(float(base_m["min"]), 2), unit, "pre_fault_24h_average"))
            if "max" in base_m and base_m["max"] is not None:
                benchmarks.append(GroundedBenchmark(target_signal, round(float(base_m["max"]), 2), unit, "pre_fault_24h_average"))
            if base_m.get("min") is not None and base_m.get("max") is not None:
                spread_b = round(float(base_m["max"]) - float(base_m["min"]), 2)
                benchmarks.append(GroundedBenchmark(target_signal, spread_b, unit, "derived_spread"))

    avail = context.get("availability_status", {})
    rollup_m = context.get("long_term_historical_rollup", {})
    is_rollup_available = (
        avail.get("historical_rollup_status", "AVAILABLE") == "AVAILABLE"
        and rollup_m.get("status") == "SUCCESS"
    )
    if is_rollup_available:
        rollup_signal = rollup_m.get("vss_path") or (target_signal if "window" in rollup_m else None)
        if not rollup_signal or rollup_signal not in CANONICAL_VSS_SIGNALS:
            return False, f"Historical rollup contains invalid canonical signal '{rollup_signal}'."

        rollup_canonical_unit = CANONICAL_VSS_UNITS[rollup_signal]
        aggs = rollup_m.get("aggregates", rollup_m)
        r_avg = aggs.get("average") if aggs.get("average") is not None else rollup_m.get("average")
        r_min = aggs.get("min") if aggs.get("min") is not None else rollup_m.get("min")
        r_max = aggs.get("max") if aggs.get("max") is not None else rollup_m.get("max")
        r_spread = aggs.get("spread")
        if r_avg is not None:
            val = round(float(r_avg), 2)
            benchmarks.append(GroundedBenchmark(rollup_signal, val, rollup_canonical_unit, "long_term_average"))
            if r_min is not None:
                benchmarks.append(GroundedBenchmark(rollup_signal, round(float(r_min), 2), rollup_canonical_unit, "long_term_average"))
            if r_max is not None:
                benchmarks.append(GroundedBenchmark(rollup_signal, round(float(r_max), 2), rollup_canonical_unit, "long_term_average"))
            if r_min is not None and r_max is not None:
                calc_spread = round(float(r_max) - float(r_min), 2)
                if r_spread is not None and abs(float(r_spread) - calc_spread) > 0.05:
                    return False, f"Derived spread mismatch in rollup: {r_spread} vs {calc_spread}."
                benchmarks.append(GroundedBenchmark(rollup_signal, calc_spread, rollup_canonical_unit, "derived_spread"))

    if "soc_val" in context and context["soc_val"] is not None:
        benchmarks.append(GroundedBenchmark(
            "Vehicle.Powertrain.TractionBattery.StateOfCharge",
            round(float(context["soc_val"]), 2),
            "percent",
            "soc_reading",
        ))

    # Derived deltas
    trig_obj = next((b for b in benchmarks if b.category == "event_trigger"), None)
    base_obj = next((b for b in benchmarks if b.category == "pre_fault_24h_average"), None)
    roll_obj = next((b for b in benchmarks if b.category == "long_term_average" and b.signal == (target_signal or "")), None)

    if trig_obj and base_obj and trig_obj.signal == base_obj.signal:
        d_val = round(trig_obj.value - base_obj.value, 2)
        benchmarks.append(GroundedBenchmark(trig_obj.signal, d_val, trig_obj.unit, "derived_delta"))

    if trig_obj and roll_obj and trig_obj.signal == roll_obj.signal:
        d_val = round(trig_obj.value - roll_obj.value, 2)
        benchmarks.append(GroundedBenchmark(trig_obj.signal, d_val, trig_obj.unit, "derived_delta"))

    # Range calculation & provenance validation
    m_any_range_km = re.search(r"(?:range|drive|distance)[^.;,!?\n]*?(\d+(?:\.\d+)?)\s*km\b", lower_text)
    if "estimated_range_km" in context or m_any_range_km:
        if re.search(r"\b(guaranteed\s+(?:remaining\s+)?range|definite\s+range|exact\s+range\s+of|certified\s+range)\b", lower_text):
            return False, "Range estimate falsely represented as guaranteed."

    if "estimated_range_km" in context:
        if "soc_val" not in context or context["soc_val"] is None:
            return False, "Range estimate requires grounded SOC measurement."
        if "nominal_range_km" not in context or not context["nominal_range_km"] or context["nominal_range_km"] <= 0:
            return False, "Range estimate requires positive configured nominal_range_km."
        expected_range = round((float(context["soc_val"]) / 100.0) * float(context["nominal_range_km"]), 1)
        if abs(float(context["estimated_range_km"]) - expected_range) > 0.2:
            return False, f"Context estimated_range_km ({context['estimated_range_km']}) does not match formula ({expected_range})."
        if not re.search(r"(?:\b(?:estimated|approximate|approx)\b|~)", lower_text):
            return False, "Range estimate must include explicit estimated/approximate wording."
        benchmarks.append(GroundedBenchmark(
            "Vehicle.Powertrain.TractionBattery.StateOfCharge",
            round(float(context["estimated_range_km"]), 2),
            "km",
            "derived_range",
        ))
    if "nominal_range_km" in context and context["nominal_range_km"]:
        benchmarks.append(GroundedBenchmark(
            "Vehicle.Powertrain.TractionBattery.StateOfCharge",
            round(float(context["nominal_range_km"]), 2),
            "km",
            "nominal_spec",
        ))

    if "soc_val" in context and "nominal_range_km" in context and context["nominal_range_km"]:
        expected_range = round((context["soc_val"] / 100.0) * context["nominal_range_km"], 1)
        m_range = re.search(r"(?:estimated\s+(?:remaining\s+)?range|~\s*|range\s*:\s*~?)\s*(\d+(?:\.\d+)?)\s*km\b", lower_text)
        if m_range:
            claimed_km = float(m_range.group(1))
            if abs(claimed_km - expected_range) > 0.2:
                return False, f"Range calculation error: claimed {claimed_km} km does not match expected {expected_range} km for SOC {context['soc_val']}%."

    # 7. Validate Extracted Factual Measurements (Signal-Aware Clause Checking)
    extracted_measurements = extract_factual_measurements(text)
    for val, unit in extracted_measurements:
        val_rounded = round(val, 2)
        matching_bm = [b for b in benchmarks if abs(val_rounded - b.value) <= 0.05 and b.unit == unit]
        if not matching_bm:
            val_any_u = [b for b in benchmarks if abs(val_rounded - b.value) <= 0.05]
            if val_any_u:
                return False, f"Unit mismatch for measurement {val}: reported as '{unit}', expected '{val_any_u[0].unit}'."
            return False, f"Unsupported measurement claim: {val} {unit} is not present in grounded evidence."

        v_str = str(val_rounded)
        clause_match = re.search(rf"[^.;,!?\n]*\b{re.escape(v_str)}\s*(?:degrees?\s+)?{re.escape(unit)}\b[^.;,!?\n]*", lower_text)
        clause_text = clause_match.group(0) if clause_match else lower_text

        for other_sig, kws in SIGNAL_AFFINITY_KEYWORDS.items():
            if not any(b.signal == other_sig for b in matching_bm):
                for kw in kws:
                    clause_affinity = re.search(rf"{kw}[^.;,!?\n]*\b{re.escape(v_str)}\b", clause_text) or \
                                      re.search(rf"\b{re.escape(v_str)}\b[^.;,!?\n]*{kw}", clause_text)
                    if clause_affinity:
                        own_kws = [okw for b in matching_bm for okw in SIGNAL_AFFINITY_KEYWORDS.get(b.signal, [])]
                        if not any(re.search(okw, clause_text) for okw in own_kws):
                            return False, f"Signal mismatch: measurement {val} {unit} falsely associated with signal '{other_sig}'."

    # 8. Provenance Attribution Check across all categories
    for b in benchmarks:
        if b.category in ("event_trigger", "pre_fault_24h_average", "long_term_average"):
            err = _check_provenance_attribution(lower_text, b.value, b.category)
            if err:
                return False, err

    # 9. Validate Remaining Unvalidated Numbers (Strict Structural Equality)
    valid_num_pool: Set[float] = {b.value for b in benchmarks}
    valid_num_pool.update(float(n) for n in PERMITTED_STRUCTURAL_NUMBERS)

    unvalidated_nums = extract_unvalidated_numbers(text, context)
    for num in unvalidated_nums:
        is_structural_int = num.is_integer() and int(num) in PERMITTED_STRUCTURAL_NUMBERS
        if is_structural_int or num in valid_num_pool:
            continue
        matched = any(abs(num - valid) <= 0.05 for valid in valid_num_pool)
        if not matched:
            return False, f"Unsupported numeric value in output: {num}"

    return True, None
