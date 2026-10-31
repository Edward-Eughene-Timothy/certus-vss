"""
persona_views.py: Role-based view formatters for CertusVSS.
Formats the Tier-1 Verified Evidence Card differently for Drivers vs Service Engineers.
"""

from typing import Dict, Any


def format_driver_view(evidence: Dict[str, Any]) -> str:
    """Format simplified, non-distracting metrics for the vehicle driver."""
    metrics = evidence.get("metrics", {})
    query_type = evidence.get("query_type", "GENERAL")
    exec_time = evidence.get("execution_time_ms", 0.0)

    # Extract key driver parameters safely
    soc_data = metrics.get("Vehicle.Powertrain.TractionBattery.StateOfCharge") or metrics.get("soc", {})
    speed_data = metrics.get("Vehicle.Speed") or metrics.get("speed", {})
    temp_data = metrics.get("Vehicle.Powertrain.TractionBattery.Temperature") or metrics.get("temp", {})

    soc_avg = soc_data.get("val_avg", "N/A") if isinstance(soc_data, dict) else getattr(soc_data, "val_avg", "N/A")
    speed_avg = speed_data.get("val_avg", "N/A") if isinstance(speed_data, dict) else getattr(speed_data, "val_avg", "N/A")
    temp_avg = temp_data.get("val_avg", "N/A") if isinstance(temp_data, dict) else getattr(temp_data, "val_avg", "N/A")

    status_tag = "[green]NORMAL[/green]"
    if isinstance(temp_avg, (int, float)) and temp_avg > 42.0:
        status_tag = "[yellow]WARM[/yellow]"

    return (
        f"[bold cyan]🚗 DRIVER CONSOLE[/bold cyan]   |   Status: {status_tag}   |   SQL Latency: [green]{exec_time} ms[/green]\n"
        f"─────────────────────────────────────────────────────────────────────────────\n"
        f"• [bold]Battery State of Charge:[/bold] {soc_avg}%\n"
        f"• [bold]Average Speed:[/bold] {speed_avg} km/h\n"
        f"• [bold]Battery Temperature:[/bold] {temp_avg} °C\n"
        f"• [bold]Active Context:[/bold] {query_type}\n"
    )


def format_engineer_view(evidence: Dict[str, Any]) -> str:
    """Format exhaustive, forensic telemetry metrics for service engineers."""
    metrics = evidence.get("metrics", {})
    exec_time = evidence.get("execution_time_ms", 0.0)
    time_window = evidence.get("time_window_str", "30d")
    events = evidence.get("events", [])

    lines = [
        f"[bold magenta]🔧 SERVICE ENGINEER FORENSIC TELEMETRY[/bold magenta]   |   Window: [yellow]{time_window}[/yellow]   |   SQL Latency: [green]{exec_time} ms[/green]",
        "─────────────────────────────────────────────────────────────────────────────",
        f"{'VSS Signal Path':<50} {'Avg':<10} {'Min':<10} {'Max':<10} {'Delta':<10}",
        "─" * 90,
    ]

    for path, data in metrics.items():
        if isinstance(data, dict):
            val_avg = f"{data.get('val_avg', 0):.2f}{data.get('unit', '')}"
            val_min = f"{data.get('val_min', 0):.2f}"
            val_max = f"{data.get('val_max', 0):.2f}"
            val_delta = f"{data.get('val_delta', 0):.2f}"
        else:
            val_avg = f"{getattr(data, 'val_avg', 0):.2f}{getattr(data, 'unit', '')}"
            val_min = f"{getattr(data, 'val_min', 0):.2f}"
            val_max = f"{getattr(data, 'val_max', 0):.2f}"
            val_delta = f"{getattr(data, 'val_delta', 0):.2f}"

        # Truncate long VSS paths cleanly
        display_path = path if len(path) <= 48 else "..." + path[-45:]
        lines.append(f"{display_path:<50} {val_avg:<10} {val_min:<10} {val_max:<10} {val_delta:<10}")

    if events:
        lines.append("─" * 90)
        lines.append(f"[bold red]Recent Fault Events (DTCs):[/bold red] {len(events)} logged")
        for ev in events[:3]:
            dtc = ev.get("dtc_code", "UNKNOWN")
            cat = ev.get("event_category", "ALERT")
            sig = ev.get("trigger_signal", "")
            lines.append(f"  • [{cat}] DTC: {dtc} tripped on {sig}")

    return "\n".join(lines)
