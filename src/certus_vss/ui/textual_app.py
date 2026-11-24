"""
Dual-tier Terminal UI for CertusVSS.
"""

from certus_vss.analytics.intent_parser import parse_intent


def execute_acquisition(parts):
    if len(parts) != 5:
        print(
            "Usage: acquire <actor_id> <signal> "
            "<start_epoch_s> <end_epoch_s>"
        )
        return

    try:
        actor_id = int(parts[1])
        signal = parts[2]
        start_epoch_s = int(parts[3])
        end_epoch_s = int(parts[4])

        if actor_id < 0 or start_epoch_s < 0 or end_epoch_s < start_epoch_s:
            raise ValueError("Invalid actor ID or time range.")

        from certus_vss.inference.llama_runtime import SLMDiagnosticRuntime

        print("Starting acquisition inference...")
        runtime = SLMDiagnosticRuntime()

        try:
            result = runtime.diagnose_acquisition_end_to_end(
                actor_id=actor_id,
                signal=signal,
                start_epoch_s=start_epoch_s,
                end_epoch_s=end_epoch_s,
                limit=5,
                deadline_ms=5000.0,
            )

            print("Evidence status:", result["diagnostics"]["evidence_status"])
            print("Summary:", result["diagnostics"]["summary"])
            print("Key facts:", result["diagnostics"]["key_facts"])
            print("Latency:", result["latency_ms"], "ms")

        finally:
            runtime.close()

    except (ValueError, TypeError) as exc:
        print("Invalid acquisition arguments:", exc)
    except Exception as exc:
        print("Acquisition request failed:", exc)


def run_app():
    print("CertusVSS Vehicle Assistant")
    print("Enter a question, or use an explicit acquisition command:")
    print("acquire <actor_id> <signal> <start_epoch_s> <end_epoch_s>")
    print("Type 'exit' or 'quit' to close.\n")

    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSession ended.")
            break

        if query.casefold() in {"exit", "quit"}:
            print("Session ended.")
            break

        if not query:
            continue

        if (
            query.casefold() == "acquire"
            or query.casefold().startswith("acquire ")
        ):
            execute_acquisition(query.split())
            continue

        try:
            result = parse_intent(query)
            print("Intent:", result["intent"])
            print("Confidence:", result["confidence"])

            if result["intent"] == "ACQUISITION":
                print(
                    "To retrieve evidence, use the explicit acquire command "
                    "with actor, signal, and time range."
                )
            elif result["intent"] == "DIAGNOSTIC":
                print("Diagnostic execution is not connected yet.")
            else:
                print("General conversation is not connected yet.")

        except ValueError as exc:
            print("Input error:", exc)


if __name__ == "__main__":
    run_app()