"""
interactive_chat.py: Interactive natural language REPL with real-time token streaming.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from certus_vss.presentation.assistant_engine import EdgeVehicleAssistant


def main():
    print("=" * 72)
    print("        CERTUS-VSS EDGE AI CONVERSATIONAL VEHICLE ASSISTANT        ")
    print("=" * 72)
    print("Loading Qwen2.5-0.5B-Instruct & attaching read-only SQLite telemetry...")

    assistant = EdgeVehicleAssistant(enable_slm=True)
    slm_status = "ACTIVE (Streaming Qwen2.5-0.5B-Instruct)" if assistant.llm else "FALLBACK (Deterministic Only)"

    print(f"[SLM Engine] : {slm_status}")
    print(f"[Active Role]: {assistant.role.upper()} (Switch with: /role driver | technician | fleet_operator)")
    print("Ask anything naturally:")
    print("  - 'What is my range?' / 'Is there any problem with my car?'")
    print("  - 'What fault is it?' / 'Why is the battery temperature high?'")
    print("  - 'What is my average speed?' / 'Is my A/C working well?'")
    print("  - Type /exit to quit.")
    print("=" * 72 + "\n")

    while True:
        try:
            prompt = input(f"\033[1;36m({assistant.role}) >> \033[0m").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break

        if not prompt:
            continue

        if prompt.lower() in {"/exit", "exit", "quit"}:
            break

        if prompt.startswith("/role"):
            parts = prompt.split()
            if len(parts) > 1 and parts[1].lower() in {"driver", "technician", "fleet_operator"}:
                assistant.set_role(parts[1].lower())
                print(f"\n[Switched Persona] Now speaking as: \033[1;32m{assistant.role.upper()}\033[0m\n")
            else:
                print("\n[Usage] /role <driver | technician | fleet_operator>\n")
            continue

        print("\n" + "-" * 68)
        # Real-time token streaming to stdout
        for token in assistant.answer_stream(prompt):
            sys.stdout.write(token)
            sys.stdout.flush()
        print("\n" + "-" * 68 + "\n")


if __name__ == "__main__":
    main()
