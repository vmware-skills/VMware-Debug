"""vmware-debug CLI — read-only incident triage from the terminal.

Correlation is local and offline: feed it events you've already collected (a
JSON file or stdin) and it returns a ranked timeline + next-check ideas. The
`mcp` subcommand starts the stdio MCP server (entry point that does not touch
the network, so it works behind corporate TLS proxies — CLAUDE.md 踩坑 #25).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from vmware_debug import __version__
from vmware_debug.mcp.tools import incident_timeline, list_symptom_categories

app = typer.Typer(
    add_completion=False,
    help="VMware diagnostic brain — read-only incident triage and root-cause routing.",
)
console = Console()


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"vmware-debug {__version__}")


@app.command()
def categories() -> None:
    """List the symptom categories debug recognises and what to check for each."""
    table = Table(title="vmware-debug symptom categories")
    table.add_column("category", style="cyan")
    table.add_column("example keywords")
    table.add_column("suggested check", style="green")
    for c in list_symptom_categories()["items"]:
        table.add_row(c["category"], ", ".join(c["example_keywords"]), c["suggested_check"])
    console.print(table)


@app.command()
def triage(
    events_file: Optional[Path] = typer.Option(
        None, "--events", "-e", help="JSON file of event envelopes; reads stdin if omitted."
    ),
    bin_seconds: Optional[float] = typer.Option(None, help="Time-bin width; auto if omitted."),
    top_n: int = typer.Option(5, help="Max hypotheses to return."),
) -> None:
    """Correlate a set of pre-collected events into a ranked incident timeline."""
    raw = events_file.read_text() if events_file else sys.stdin.read()
    try:
        events = json.loads(raw)
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON:[/red] {exc}. Provide a JSON array of event envelopes.")
        raise typer.Exit(code=2) from exc
    if not isinstance(events, list):
        console.print("[red]Expected a JSON array of event envelopes.[/red]")
        raise typer.Exit(code=2)

    try:
        result = incident_timeline(events, bin_seconds=bin_seconds, top_n=top_n)
    except ValueError as exc:
        console.print(f"[red]Could not correlate events:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    console.print_json(data=result)


@app.command()
def mcp() -> None:
    """Start the stdio MCP server (no network access; proxy-safe)."""
    from mcp_server.server import main as _main

    _main()


if __name__ == "__main__":
    app()
