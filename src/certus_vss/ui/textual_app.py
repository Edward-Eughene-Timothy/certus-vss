"""
textual_app.py: Fully wired dual-tier TUI dashboard for CertusVSS.
- Wires SQLite Deterministic Query Engine for instant Tier-1 Evidence Cards.
- Wires Edge SLM Inference Engine for Tier-2 natural language explanations.
"""

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Header, Footer, Static, Input, Button
from textual.binding import Binding
from textual.worker import Worker

from certus_vss.ui.persona_views import format_driver_view, format_engineer_view
from certus_vss.analytics.query_engine import query_telemetry_summary
from certus_vss.models.inference_engine import CertusInferenceEngine


class EvidenceCard(Static):
    """Top panel rendering deterministic SQLite facts instantly."""
    pass


class SLMExplanationPanel(Static):
    """Bottom panel rendering the natural language output."""
    pass


class CertusVSSApp(App):
    """Main Textual TUI Application for CertusVSS."""

    CSS = """
    Screen {
        background: #11111b;
        color: #cdd6f4;
    }
    Header {
        background: #1e1e2e;
        color: #89b4fa;
    }
    Footer {
        background: #1e1e2e;
    }
    #main-container {
        layout: vertical;
        padding: 1;
    }
    .panel-box {
        border: solid #313244;
        background: #181825;
        padding: 1;
        margin-bottom: 1;
    }
    #evidence-panel {
        height: 1fr;
    }
    #explanation-panel {
        height: 1fr;
    }
    #input-bar {
        height: 3;
        background: #1e1e2e;
        margin-top: 1;
    }
    Footer {
        background: #1e1e2e;
        dock: bottom;
    }
    Input {
        width: 1fr;
        background: #313244;
        color: #cdd6f4;
        border: none;
    }
    Button {
        background: #89b4fa;
        color: #11111b;
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("f1", "toggle_persona", "Toggle Persona (Driver / Engineer)", show=True),
        Binding("q", "quit", "Quit", show=True),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("ctrl+q", "quit", "Quit", show=False),
        Binding("escape", "quit", "Quit", show=False),
    ]

    def __init__(self):
        super().__init__()
        self.current_persona = "DRIVER"  # Options: DRIVER, ENGINEER
        self.inference_engine = None
        self.latest_evidence = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-container"):
            # Top Panel: Verified Evidence
            with Vertical(classes="panel-box", id="evidence-panel"):
                yield Static("[bold]📊 Tier-1 Verified Evidence Card (SQLite Deterministic Core)[/bold]", id="evidence-title")
                yield EvidenceCard("Initializing database query...", id="evidence-content")
            
            # Bottom Panel: SLM Explanation
            with Vertical(classes="panel-box", id="explanation-panel"):
                yield Static("[bold]🤖 Tier-2 SLM Natural Language Explanation (Qwen 2.5 0.5B)[/bold]", id="explanation-title")
                yield SLMExplanationPanel("Waiting for SLM runtime initialization...", id="explanation-content")

        # Command Input Bar
        with Horizontal(id="input-bar"):
            yield Input(placeholder="Type query or press enter (e.g. 'battery status')...", id="query-input")
            yield Button("Submit", id="submit-btn", variant="primary")
            
        yield Footer()

    def on_mount(self) -> None:
        self.title = "CertusVSS: Edge Automotive Intelligence"
        self.sub_title = f"Persona: {self.current_persona}"
        
        # Initialize SLM engine and execute initial query in background thread worker
        self.run_worker(self.initialize_and_query, thread=True, exclusive=True)

    def initialize_and_query(self) -> None:
        """Loads the SLM model and performs the initial 30-day database summary."""
        # 1. Initialize Inference Engine
        if not self.inference_engine:
            self.inference_engine = CertusInferenceEngine()

        # 2. Run Deterministic SQLite Query (< 15ms)
        self.latest_evidence = query_telemetry_summary(days=30)
        self.latest_evidence["query_type"] = "BATTERY_HEALTH"

        # 3. Update UI Top Panel instantly
        self.call_from_thread(self.render_evidence_and_trigger_slm)

    def render_evidence_and_trigger_slm(self) -> None:
        evidence_widget = self.query_one("#evidence-content", EvidenceCard)
        explanation_widget = self.query_one("#explanation-content", SLMExplanationPanel)

        # Render Tier-1 Card based on Persona
        if self.current_persona == "DRIVER":
            evidence_widget.update(format_driver_view(self.latest_evidence))
        else:
            evidence_widget.update(format_engineer_view(self.latest_evidence))

        explanation_widget.update("[yellow]Generating SLM explanation (watchdog active)...[/yellow]")
        
        # Trigger SLM generation in background thread worker
        self.run_worker(self.run_inference_worker, thread=True, exclusive=False)

    def run_inference_worker(self) -> None:
        explanation = self.inference_engine.explain_evidence(
            self.latest_evidence, 
            persona=self.current_persona, 
            timeout_ms=1500
        )
        self.call_from_thread(self.update_explanation_display, explanation)

    def update_explanation_display(self, text: str) -> None:
        explanation_widget = self.query_one("#explanation-content", SLMExplanationPanel)
        explanation_widget.update(text)

    def action_toggle_persona(self) -> None:
        """Switch between Driver and Service Engineer personas."""
        self.current_persona = "ENGINEER" if self.current_persona == "DRIVER" else "DRIVER"
        self.sub_title = f"Persona: {self.current_persona}"
        if self.latest_evidence:
            self.render_evidence_and_trigger_slm()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        self.handle_user_submission()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self.handle_user_submission()

    def handle_user_submission(self) -> None:
        query_input = self.query_one("#query-input", Input)
        val = query_input.value.strip().lower()
        
        if "temp" in val or "thermal" in val:
            self.latest_evidence["query_type"] = "THERMAL_WARNING"
        else:
            self.latest_evidence["query_type"] = "BATTERY_HEALTH"
            
        query_input.value = ""
        self.render_evidence_and_trigger_slm()


if __name__ == "__main__":
    app = CertusVSSApp()
    app.run()
