"""
assistant_engine.py: Edge AI Conversational Vehicle Assistant (Phase 4.2C).
Features structured ConversationState, deterministic active-event anchoring,
follow-up pronoun resolution, persona consistency, signal-specific rollup
windows, and strict post-generation grounding validation across all branches.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Tuple

from certus_vss.analytics.context_builder import ConversationContextBuilder
from certus_vss.analytics.conversation_state import ConversationState
from certus_vss.analytics.evidence_builder import EvidenceBuilder, EvidenceMissingError
from certus_vss.analytics.grounding_validator import validate_output_grounding
from certus_vss.analytics.intent_parser import parse_intent
from certus_vss.analytics.query_engine import DeterministicQueryEngine
from certus_vss.catalog.dtc_catalog import PROVISIONAL_DTC_CATALOG, get_dtc_reference
from certus_vss.catalog.signals import CANONICAL_VSS_SIGNALS, CANONICAL_VSS_UNITS, get_signal_unit
from certus_vss.presentation.answer_formatter import render_persona_report
from certus_vss.presentation.persona_formatter import format_persona_view
from certus_vss.storage.db_engine import DB_PATH, get_readonly_connection

DEFAULT_MODEL_PATH = Path("/app/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")

PROVISIONAL_SERVICE_LOOKUP: Dict[str, str] = {
    code: entry["service_procedure"]
    for code, entry in PROVISIONAL_DTC_CATALOG.items()
}


class EdgeVehicleAssistant:
    def __init__(
        self,
        db_path: Optional[Path] = None,
        model_path: Optional[Path] = None,
        enable_slm: bool = True,
        vehicle_id: Optional[str] = None,
        nominal_range_km: Optional[float] = None,
    ) -> None:
        self.db_path = db_path or DB_PATH
        self.nominal_range_km = nominal_range_km
        self.query_engine = DeterministicQueryEngine(db_path=self.db_path)
        self.evidence_builder = EvidenceBuilder(db_path=self.db_path)
        self.role: str = "driver"
        self.state = ConversationState(active_role=self.role, active_vehicle_id=vehicle_id)
        self.llm = None

        if enable_slm:
            mpath = model_path or DEFAULT_MODEL_PATH
            if mpath.exists():
                try:
                    from llama_cpp import Llama
                    self.llm = Llama(
                        model_path=str(mpath),
                        n_ctx=768,
                        n_threads=2,
                        n_batch=64,
                        use_mmap=True,
                        verbose=False,
                    )
                except Exception as exc:
                    print(f"[Warning] Could not load SLM: {exc}")

    def set_role(self, role: str) -> None:
        if role not in {"driver", "technician", "fleet_operator"}:
            raise ValueError(f"Unsupported role: {role}")
        self.role = role
        self.state.set_role(role)

    def switch_vehicle(self, vehicle_id: Optional[str]) -> None:
        self.state.switch_vehicle(vehicle_id)

    def get_signal_time_bounds(self, vss_path: str) -> Optional[Tuple[int, int]]:
        """Returns signal-specific actual (min_epoch_s, max_epoch_s) from telemetry_rollup_1m."""
        try:
            with get_readonly_connection(self.db_path) as conn:
                row = conn.execute(
                    "SELECT MIN(bucket_epoch_m) as min_m, MAX(bucket_epoch_m) as max_m "
                    "FROM telemetry_rollup_1m WHERE vss_path = ?",
                    (vss_path,),
                ).fetchone()
                if row and row["min_m"] is not None and row["max_m"] is not None:
                    return int(row["min_m"]) * 60, (int(row["max_m"]) + 1) * 60
        except Exception:
            pass
        return None

    def _get_long_term_rollup(self, vss_path: str) -> Dict[str, Any]:
        bounds = self.get_signal_time_bounds(vss_path)
        if bounds is None:
            return {"status": "NO_DATA", "vss_path": vss_path}
        start_s, end_s = bounds
        metrics = self.query_engine.get_signal_metrics(vss_path, start_s, end_s)
        if metrics.get("status") == "SUCCESS":
            metrics["start_epoch_s"] = start_s
            metrics["end_epoch_s"] = end_s
            metrics["vss_path"] = vss_path
        return metrics

    def _build_diagnostic_context_for_event(
        self, event_id: int
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
        try:
            evidence = self.evidence_builder.build_event_evidence(event_id)
            trigger_sig = evidence["event"]["trigger_signal"]
            rollup = self._get_long_term_rollup(trigger_sig)
            context = ConversationContextBuilder.build_grounded_context(
                evidence=evidence,
                historical_rollup=rollup,
                role=self.role,
            )
            if context and "long_term_historical_rollup" in context and context["long_term_historical_rollup"].get("status") == "SUCCESS":
                context["long_term_historical_rollup"]["vss_path"] = trigger_sig
            return evidence, context, None
        except EvidenceMissingError as exc:
            return None, None, f"Referenced event #{event_id} evidence is unavailable or incomplete: {exc}"
        except Exception as exc:
            return None, None, f"Failed to retrieve evidence for event #{event_id}: {exc}"

    def _build_latest_diagnostic_context(
        self,
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[str]]:
        try:
            events_res = self.query_engine.get_recent_diagnostic_events(limit=1)
            events = events_res.get("events", [])
            if not events:
                return None, None, "No diagnostic fault events are recorded in the telemetry database."
            latest_id = int(events[0]["event_id"])
            return self._build_diagnostic_context_for_event(latest_id)
        except Exception as exc:
            return None, None, f"Diagnostic event query failed: {exc}"

    @staticmethod
    def _format_provenance_block(context: Dict[str, Any]) -> str:
        snap = context["active_fault_snapshot"]
        trig = snap["trigger_measurement"]
        base = snap["pre_fault_24h_baseline"]
        avail = context["availability_status"]
        rollup = context.get("long_term_historical_rollup", {})

        lines = [
            "Measurement Provenance Summary:",
            f"  • Event trigger measurement: {trig['value']} {trig['unit']} ({trig['provenance']})",
            f"  • 24-hour pre-fault baseline average: {base['average']} {base['unit']} "
            f"(min {base['min']} {base['unit']}, max {base['max']} {base['unit']}; {base['window']})",
        ]
        if avail.get("historical_rollup_status") == "AVAILABLE" and rollup.get("status") == "SUCCESS":
            lines.append(
                f"  • Long-term recorded database average: {rollup['average']} {rollup['unit']} "
                f"(min {rollup['min']} {rollup['unit']}, max {rollup['max']} {rollup['unit']}; {rollup['window']})"
            )
        else:
            lines.append(
                "  • Long-term recorded database average: UNAVAILABLE "
                "(comparison across the complete recorded database window cannot be established)"
            )
        return "\n".join(lines)

    def generate_validated_slm_text(
        self,
        system_instruction: str,
        user_query: str,
        context: Dict[str, Any],
        fallback_text: str,
    ) -> str:
        if not self.llm:
            return fallback_text

        chatml = (
            f"<|im_start|>system\n{system_instruction}<|im_end|>\n"
            f"<|im_start|>user\n{user_query}<|im_end|>\n"
            f"<|im_start|>assistant\n"
        )
        try:
            res = self.llm(
                chatml,
                max_tokens=140,
                temperature=0.15,
                top_p=0.9,
                stop=["<|im_end|>", "\n\n\n"],
            )
            raw_text = res["choices"][0]["text"].strip()
            is_valid, _ = validate_output_grounding(raw_text, context)
            if is_valid:
                return raw_text
            return fallback_text
        except Exception:
            return fallback_text

    def _finalize_and_stream(
        self,
        complete_response: str,
        context: Dict[str, Any],
        fallback_response: str,
    ) -> Generator[str, None, None]:
        """
        Universal response boundary: Validates the complete response before yielding.
        If validation fails, discards the unverified text and streams the validated fallback.
        """
        is_valid, _ = validate_output_grounding(complete_response, context)
        if is_valid:
            yield complete_response
        else:
            is_fallback_valid, _ = validate_output_grounding(fallback_response, context)
            if is_fallback_valid:
                yield fallback_response
            else:
                yield "[Diagnostic Evidence Unavailable] Response could not be validated against grounded telemetry context."

    def answer_stream(self, user_query: str) -> Generator[str, None, None]:
        """
        Main query router with state-aware follow-up resolution, event anchoring,
        and universal output grounding across all branches.
        """
        t0 = time.perf_counter()
        parsed = parse_intent(user_query, state=self.state)
        intent = parsed["intent"]
        vss_path = parsed.get("vss_path")
        self.state.last_intent = intent

        # 1. Greetings
        if intent == "GREETING":
            veh_str = f"Vehicle: {self.state.active_vehicle_id}. " if self.state.active_vehicle_id else ""
            assembled = (
                f"Hello! I am your CertusVSS assistant ({self.role.upper()} mode). {veh_str}"
                "I monitor canonical EV telemetry signals and diagnostic events. "
                "How can I assist you right now?"
            )
            ctx = {"active_fault_snapshot": {}, "role": self.role}
            yield from self._finalize_and_stream(assembled, ctx, assembled)
            return

        # 2. Out of Scope Check
        if intent == "OUT_OF_SCOPE":
            reason = parsed.get("reason", "This parameter is not tracked.")
            assembled = (
                f"[Boundary Notice] {reason}\n"
                f"Monitored canonical signals: {', '.join(CANONICAL_VSS_SIGNALS)}."
            )
            ctx = {"active_fault_snapshot": {}, "role": self.role}
            yield from self._finalize_and_stream(assembled, ctx, assembled)
            return

        # 3. Estimated Range Query
        if intent == "RANGE_ESTIMATE":
            soc_res = self._get_long_term_rollup("Vehicle.Powertrain.TractionBattery.StateOfCharge")
            if soc_res.get("status") != "SUCCESS":
                msg = (
                    "State of Charge telemetry is unavailable in the database; "
                    "remaining range cannot be estimated."
                )
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            soc_val = soc_res["aggregates"]["average"]
            active_eid = self.state.active_event_id
            fault_note = ""
            active_ctx = None
            if active_eid is not None:
                _, active_ctx, _ = self._build_diagnostic_context_for_event(active_eid)
                if active_ctx is not None:
                    snap = active_ctx["active_fault_snapshot"]
                    dtc_label = snap["dtc_code"] or "UNASSIGNED"
                    fault_note = (
                        f"\nNote: Active pinned event #{active_eid} ({dtc_label} on {snap['trigger_signal']} at "
                        f"{snap['trigger_measurement']['value']} {snap['trigger_measurement']['unit']}) "
                        "is under review and may affect discharge performance."
                    )

            range_ctx: Dict[str, Any] = {
                "active_fault_snapshot": active_ctx["active_fault_snapshot"] if active_ctx else {},
                "soc_val": soc_val,
                "soc_unit": "percent",
                "soc_signal": "Vehicle.Powertrain.TractionBattery.StateOfCharge",
            }

            if self.nominal_range_km is not None and self.nominal_range_km > 0:
                est_km = round((soc_val / 100.0) * self.nominal_range_km, 1)
                range_ctx["estimated_range_km"] = est_km
                range_ctx["nominal_range_km"] = self.nominal_range_km
                reply = (
                    f"Estimated Remaining Range: ~{est_km} km (based on recorded State of Charge of {soc_val}% "
                    f"and configured nominal range of {self.nominal_range_km} km).{fault_note}"
                )
                fallback = f"Recorded State of Charge average is {soc_val}%. Estimated remaining range is ~{est_km} km."
            else:
                reply = (
                    f"Recorded State of Charge average is {soc_val}%. Nominal full-charge vehicle range "
                    f"specification is not configured, so remaining driving range cannot currently be established.{fault_note}"
                )
                fallback = f"Recorded State of Charge average is {soc_val}%. Remaining driving range is unconfigured."

            yield from self._finalize_and_stream(reply, range_ctx, fallback)
            return

        # 4. New Diagnostic Inquiry
        if intent == "NEW_DIAGNOSTIC":
            evidence, context, err = self._build_latest_diagnostic_context()
            if evidence is None or context is None:
                err_msg = f"[Diagnostic Evidence Unavailable] {err}"
                yield from self._finalize_and_stream(err_msg, {"active_fault_snapshot": {}}, err_msg)
                return

            eid = int(evidence["event"]["event_id"])
            trigger_sig = evidence["event"]["trigger_signal"]
            dtc = evidence["event"].get("dtc_code")
            val = evidence["event"]["trigger_value"]

            base_avg = context["active_fault_snapshot"]["pre_fault_24h_baseline"]["average"]
            rollup_avg = None
            if context.get("availability_status", {}).get("historical_rollup_status") == "AVAILABLE":
                rollup_avg = context.get("long_term_historical_rollup", {}).get("average")

            semantic_nums = {"event_trigger": val, "pre_fault_24h_average": base_avg}
            if rollup_avg is not None:
                semantic_nums["long_term_average"] = rollup_avg

            self.state.pin_event(
                event_id=eid,
                vss_path=trigger_sig,
                dtc_code=dtc,
                semantic_numbers=semantic_nums,
            )

            diagnostic = {
                "summary": f"Fault {dtc or 'EVENT'} at {val}",
                "key_facts": [f"Trigger value {val}"],
                "evidence_status": "VERIFIED",
            }

            structured_view = format_persona_view(
                evidence=evidence,
                diagnostic=diagnostic,
                role=self.role,
                service_manual_lookup=PROVISIONAL_SERVICE_LOOKUP,
            )
            card_report = render_persona_report(structured_view)
            prov_block = self._format_provenance_block(context)
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            assembled_card = (
                f"--- Certified {self.role.upper()} Telemetry View (Event #{eid}, {elapsed_ms:.1f} ms) ---\n"
                f"{card_report}\n\n"
                f"{prov_block}"
            )

            slm_segment = ""
            if self.llm:
                reconcile = context["provenance_reconciliation"]
                sys_prompt = (
                    f"You are CertusVSS speaking to a {self.role}. "
                    f"Summarize in one or two concise sentences only the verified measurements: "
                    f"{reconcile['distinction']}"
                )
                slm_out = self.generate_validated_slm_text(
                    system_instruction=sys_prompt,
                    user_query=user_query,
                    context=context,
                    fallback_text=reconcile["distinction"],
                )
                slm_segment = f"\n\n{slm_out}"

            complete_response = f"{assembled_card}{slm_segment}"
            reconcile = context["provenance_reconciliation"]
            fallback_response = f"--- Certified {self.role.upper()} Telemetry View (Event #{eid}) ---\n{prov_block}\n\n{reconcile['distinction']}"
            yield from self._finalize_and_stream(complete_response, context, fallback_response)
            return

        # 5. Follow-Up: Comparison
        if intent == "FOLLOWUP_COMPARISON":
            target_eid = self.state.active_event_id
            if target_eid is None:
                msg = "[Diagnostic Evidence Unavailable] No active diagnostic event is currently pinned. Please ask to check vehicle diagnostics first."
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            evidence, context, err = self._build_diagnostic_context_for_event(target_eid)
            if context is None:
                msg = f"[Diagnostic Evidence Unavailable] {err}"
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            snap = context["active_fault_snapshot"]
            trig = snap["trigger_measurement"]
            base = snap["pre_fault_24h_baseline"]
            rollup = context.get("long_term_historical_rollup", {})
            reconcile = context["provenance_reconciliation"]

            reply_lines = [
                f"Comparison for Pinned Event #{target_eid} ({snap['trigger_signal']}):",
                f"  • Event trigger measurement: {trig['value']} {trig['unit']}",
                f"  • 24-hour pre-fault baseline average: {base['average']} {base['unit']}",
            ]
            if rollup.get("status") == "SUCCESS":
                delta_base = round(trig["value"] - base["average"], 2)
                delta_roll = round(trig["value"] - rollup["average"], 2)
                reply_lines.append(f"  • Long-term recorded database average: {rollup['average']} {rollup['unit']}")
                reply_lines.append(f"  • Observation: Trigger measurement is {delta_base} {trig['unit']} above 24h baseline and {delta_roll} {trig['unit']} above long-term database average.")
            else:
                reply_lines.append("  • Long-term recorded database average: UNAVAILABLE")
                reply_lines.append(f"  • Observation: Trigger measurement is {round(trig['value'] - base['average'], 2)} {trig['unit']} above 24h baseline.")

            reply_lines.append("")
            reply_lines.append(reconcile["distinction"])
            assembled_reply = "\n".join(reply_lines)
            fallback_reply = reconcile["distinction"]

            yield from self._finalize_and_stream(assembled_reply, context, fallback_reply)
            return

        # 6. Follow-Up: Procedure
        if intent == "FOLLOWUP_PROCEDURE":
            target_eid = self.state.active_event_id
            if target_eid is None:
                msg = "[Diagnostic Evidence Unavailable] No active diagnostic event is currently pinned. Please ask to check vehicle diagnostics first."
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            evidence, context, err = self._build_diagnostic_context_for_event(target_eid)
            if context is None:
                msg = f"[Diagnostic Evidence Unavailable] {err}"
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            snap = context["active_fault_snapshot"]
            dtc = snap["dtc_code"] or "UNASSIGNED"
            proc = snap["service_procedure"]
            status = snap["dtc_reference_status"]

            if status == "PROVISIONAL_REFERENCE":
                reply = (
                    f"Inspection Procedure Reference (Event #{target_eid}, DTC {dtc}):\n"
                    f"  • Status: PROVISIONAL_REFERENCE (Unconfirmed against OEM documentation)\n"
                    f"  • Procedure Reference: {proc}\n"
                    "Note: This procedure is a provisional catalog reference and requires verification against official OEM workshop manuals."
                )
            else:
                reply = (
                    f"Inspection Procedure for Event #{target_eid} (DTC {dtc}):\n"
                    "No provisional procedure reference is mapped for this code in the local catalog. "
                    f"Observed trigger signal was {snap['trigger_signal']} at {snap['trigger_measurement']['value']} {snap['trigger_measurement']['unit']}."
                )

            fallback = f"Inspection Procedure Reference (Event #{target_eid}): Procedure requires OEM manual verification."
            yield from self._finalize_and_stream(reply, context, fallback)
            return

        # 7. Follow-Up: Baseline
        if intent == "FOLLOWUP_BASELINE":
            target_eid = self.state.active_event_id
            if target_eid is None:
                msg = "[Diagnostic Evidence Unavailable] No active diagnostic event is currently pinned. Please ask to check vehicle diagnostics first."
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            evidence, context, err = self._build_diagnostic_context_for_event(target_eid)
            if context is None:
                msg = f"[Diagnostic Evidence Unavailable] {err}"
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            snap = context["active_fault_snapshot"]
            base = snap["pre_fault_24h_baseline"]
            rollup = context.get("long_term_historical_rollup", {})

            lines = [
                f"Baseline Metrics for Pinned Event #{target_eid} ({snap['trigger_signal']}):",
                f"  • 24-hour pre-fault baseline average: {base['average']} {base['unit']} (min {base['min']} {base['unit']}, max {base['max']} {base['unit']})",
            ]
            if rollup.get("status") == "SUCCESS":
                lines.append(f"  • Long-term recorded database average: {rollup['average']} {rollup['unit']} (min {rollup['min']} {rollup['unit']}, max {rollup['max']} {rollup['unit']})")
            else:
                lines.append("  • Long-term recorded database average: UNAVAILABLE")

            assembled_base = "\n".join(lines)
            fallback = f"Baseline Metrics (Event #{target_eid}): 24-hour baseline average: {base['average']} {base['unit']}."
            yield from self._finalize_and_stream(assembled_base, context, fallback)
            return

        # 8. Follow-Up: DTC / Fault Explanation
        if intent in ("FOLLOWUP_EXPLANATION", "DTC_QUERY"):
            target_eid = self.state.active_event_id
            target_dtc = parsed.get("dtc_code") or self.state.last_dtc_code

            if target_eid is None and target_dtc:
                ref = get_dtc_reference(target_dtc)
                if ref:
                    reply = (
                        f"DTC Reference: {target_dtc} — {ref['title']}\n"
                        f"  • Status: PROVISIONAL_REFERENCE (Unconfirmed against OEM documentation)\n"
                        f"  • Description: {ref['description']}\n"
                        f"  • Procedure Reference: {ref['service_procedure']}"
                    )
                else:
                    reply = f"DTC '{target_dtc}' is not present in the local provisional reference catalog."
                ctx = {"active_fault_snapshot": {"dtc_code": target_dtc}}
                yield from self._finalize_and_stream(reply, ctx, reply)
                return

            if target_eid is None:
                msg = "[Diagnostic Evidence Unavailable] No active diagnostic event is currently pinned. Please ask to check vehicle diagnostics first."
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            evidence, context, err = self._build_diagnostic_context_for_event(target_eid)
            if context is None:
                msg = f"[Diagnostic Evidence Unavailable] {err}"
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            snap = context["active_fault_snapshot"]
            dtc_code = snap["dtc_code"] or "UNASSIGNED"
            dtc_status = snap["dtc_reference_status"]

            if dtc_status == "PROVISIONAL_REFERENCE":
                ref_block = (
                    f"Explanation for Pinned Event #{target_eid} (DTC {dtc_code}):\n"
                    f"  • Status: PROVISIONAL_REFERENCE (Unconfirmed against OEM documentation)\n"
                    f"  • Title: {snap['dtc_title']}\n"
                    f"  • Description: {snap['dtc_description']}\n"
                    f"  • Procedure Reference: {snap['service_procedure']}"
                )
            else:
                ref_block = (
                    f"Pinned Event #{target_eid} (Code: {dtc_code}):\n"
                    "This code is not in the provisional reference catalog. Only verified telemetry is recorded."
                )

            prov_block = self._format_provenance_block(context)
            assembled_dtc_reply = f"{ref_block}\n\n{prov_block}\n"

            if self.llm:
                reconcile = context["provenance_reconciliation"]
                sys_prompt = (
                    f"You are CertusVSS speaking to a {self.role}. "
                    f"Explain ONLY these verified facts for Event #{target_eid}: "
                    f"{reconcile['distinction']} DTC status: {dtc_status}. "
                    f"Description: {snap['dtc_description'] or 'Not in reference catalog'}."
                )
                slm_out = self.generate_validated_slm_text(
                    system_instruction=sys_prompt,
                    user_query=user_query,
                    context=context,
                    fallback_text=reconcile["distinction"],
                )
                assembled_dtc_reply = f"{assembled_dtc_reply}\n{slm_out}"

            fallback_dtc = f"{ref_block}\n\n{prov_block}"
            yield from self._finalize_and_stream(assembled_dtc_reply, context, fallback_dtc)
            return

        # 9. Fleet Dispatch / Trip Readiness
        if intent == "DISPATCH_CHECK":
            soc_res = self._get_long_term_rollup("Vehicle.Powertrain.TractionBattery.StateOfCharge")
            soc_val = soc_res["aggregates"]["average"] if soc_res.get("status") == "SUCCESS" else None
            soc_str = f"{soc_val}%" if soc_val is not None else "UNAVAILABLE"

            target_eid = self.state.active_event_id
            if target_eid is not None:
                evidence, context, err = self._build_diagnostic_context_for_event(target_eid)
            else:
                evidence, context, err = self._build_latest_diagnostic_context()

            if context is None:
                msg = (
                    f"Dispatch Check ({self.role.upper()}): State of Charge is {soc_str}. "
                    f"Diagnostic event status: {err}"
                )
                ctx_empty: Dict[str, Any] = {"active_fault_snapshot": {}}
                if soc_val is not None:
                    ctx_empty["soc_val"] = soc_val
                yield from self._finalize_and_stream(msg, ctx_empty, msg)
                return

            if soc_val is not None:
                context["soc_val"] = soc_val

            snap = context["active_fault_snapshot"]
            trig = snap["trigger_measurement"]
            base = snap["pre_fault_24h_baseline"]
            dtc_code = snap["dtc_code"] or "UNASSIGNED"
            proc_note = (
                snap["service_procedure"]
                if snap["dtc_reference_status"] == "PROVISIONAL_REFERENCE"
                else "No provisional procedure reference available in catalog."
            )

            if self.role == "fleet_operator":
                reply = (
                    "--- Fleet Dispatch Triage ---\n"
                    "STATUS: FLAGGED_FOR_INSPECTION\n"
                    f"• Recorded Fault Event: #{snap['event_id']} (DTC {dtc_code} on {snap['trigger_signal']})\n"
                    f"• Event trigger measurement: {trig['value']} {trig['unit']}\n"
                    f"• 24-hour pre-fault baseline average: {base['average']} {base['unit']}\n"
                    f"• Recorded State of Charge average: {soc_str}\n"
                    f"• Provisional Reference Notice: {proc_note}"
                )
            elif self.role == "technician":
                reply = (
                    f"Technician Pre-Dispatch Check: Event #{snap['event_id']} recorded DTC {dtc_code} on "
                    f"{snap['trigger_signal']} (Event trigger measurement: {trig['value']} {trig['unit']} vs "
                    f"24-hour pre-fault baseline average: {base['average']} {base['unit']}). "
                    f"Recorded SOC average: {soc_str}. {proc_note}"
                )
            else:
                reply = (
                    f"Recorded State of Charge average is {soc_str}. "
                    f"A diagnostic event (#{snap['event_id']}, DTC {dtc_code}) is recorded on {snap['trigger_signal']} "
                    f"with an event trigger measurement of {trig['value']} {trig['unit']} "
                    f"(compared to a 24-hour pre-fault baseline average of {base['average']} {base['unit']}). "
                    "Please review vehicle service guidance before extended operation."
                )

            fallback = f"Dispatch Check: Event #{snap['event_id']} recorded on {snap['trigger_signal']}. SOC is {soc_str}."
            yield from self._finalize_and_stream(reply, context, fallback)
            return

        # 10. Telemetry Metric Query
        if intent == "TELEMETRY" and vss_path:
            if vss_path not in CANONICAL_VSS_SIGNALS:
                msg = (
                    f"[Boundary Notice] Signal '{vss_path}' is not in the canonical 6-signal EV catalog.\n"
                    f"Monitored canonical signals: {', '.join(CANONICAL_VSS_SIGNALS)}."
                )
                yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
                return

            self.state.active_vss_path = vss_path
            metrics = self._get_long_term_rollup(vss_path)
            unit = metrics.get("unit") or get_signal_unit(vss_path) or ""

            target_eid = self.state.active_event_id
            if target_eid is not None:
                _, context, _ = self._build_diagnostic_context_for_event(target_eid)
            else:
                _, context, _ = self._build_latest_diagnostic_context()

            if metrics.get("status") == "SUCCESS":
                agg = metrics["aggregates"]
                mins = metrics["window_minutes"]
                lines = [
                    f"Long-term recorded database average for {vss_path} (over {mins} recorded minutes): {agg['average']} {unit}",
                    f"  • Long-term recorded database average: {agg['average']} {unit}",
                    f"  • Long-term recorded minimum: {agg['min']} {unit}",
                    f"  • Long-term recorded maximum: {agg['max']} {unit}",
                    f"  • Long-term recorded spread:  {agg['spread']} {unit} (Query time: {metrics['execution_ms']} ms)",
                ]

                if context is not None and context["active_fault_snapshot"]["trigger_signal"] == vss_path:
                    lines.append("")
                    lines.append(self._format_provenance_block(context))

                assembled_telemetry = "\n".join(lines)
                dummy_ctx: Dict[str, Any] = {
                    "active_fault_snapshot": context["active_fault_snapshot"] if context else {},
                    "long_term_historical_rollup": metrics,
                    "availability_status": {"historical_rollup_status": "AVAILABLE"},
                }
                fallback = f"Long-term average for {vss_path}: {agg['average']} {unit}."
                yield from self._finalize_and_stream(assembled_telemetry, dummy_ctx, fallback)
            else:
                if context is not None and context["active_fault_snapshot"]["trigger_signal"] == vss_path:
                    msg = (
                        f"Long-term recorded database rollup is unavailable for {vss_path}.\n\n"
                        f"{self._format_provenance_block(context)}"
                    )
                    yield from self._finalize_and_stream(msg, context, msg)
                else:
                    msg = f"No telemetry rollup data found for {vss_path} in the database."
                    yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)
            return

        # 11. Conversational Fallback
        target_eid = self.state.active_event_id
        if target_eid is not None:
            _, context, _ = self._build_diagnostic_context_for_event(target_eid)
            if context is not None:
                prov_block = self._format_provenance_block(context)
                if self.llm:
                    reconcile = context["provenance_reconciliation"]
                    snap = context["active_fault_snapshot"]
                    sys_prompt = (
                        f"You are CertusVSS, an in-vehicle edge assistant speaking to a {self.role}. "
                        f"Verified context: Active Pinned Event #{target_eid} (DTC={snap['dtc_code'] or 'NONE'} on {snap['trigger_signal']}). "
                        f"{reconcile['distinction']} "
                        f"Canonical signals: {', '.join(CANONICAL_VSS_SIGNALS)}. "
                        "Answer directly and concisely in 1 to 2 sentences using only these facts."
                    )
                    slm_out = self.generate_validated_slm_text(
                        system_instruction=sys_prompt,
                        user_query=user_query,
                        context=context,
                        fallback_text=reconcile["distinction"],
                    )
                    assembled_fallback = f"CertusVSS ({self.role.upper()} mode, Event #{target_eid}):\n{slm_out}"
                    fallback_safe = f"CertusVSS ({self.role.upper()} mode, Event #{target_eid}):\n{prov_block}"
                    yield from self._finalize_and_stream(assembled_fallback, context, fallback_safe)
                    return
                msg = f"CertusVSS ({self.role.upper()} mode, Event #{target_eid}):\n{prov_block}"
                yield from self._finalize_and_stream(msg, context, msg)
                return

        msg = (
            f"CertusVSS ({self.role.upper()} mode): Monitoring canonical EV signals "
            f"({', '.join(CANONICAL_VSS_SIGNALS)}). No active diagnostic fault events are recorded."
        )
        yield from self._finalize_and_stream(msg, {"active_fault_snapshot": {}}, msg)

    def answer(self, user_query: str) -> str:
        return "".join(self.answer_stream(user_query))
