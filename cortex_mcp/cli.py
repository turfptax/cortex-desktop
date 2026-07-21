"""Cortex CLI - interact with the Cortex core over HTTPS.

Usage:
    cortex-cli ping
    cortex-cli context
    cortex-cli note "My note text" --project cortex --tags idea,important
    cortex-cli query notes --limit 5
"""

import json
import os
import socket
import platform
from pathlib import Path

import click

from cortex_mcp.protocol import send_command


def _get_bridge(ctx):
    """Single transport: the HTTP bridge to the Cortex core."""
    from cortex_mcp.wifi_bridge import WiFiBridge
    return WiFiBridge()


@click.group()
@click.option("--timeout", envvar="CORTEX_TIMEOUT", default=5.0, type=float, help="Response timeout in seconds.")
@click.pass_context
def cli(ctx, timeout):
    """Cortex CLI - interact with the Cortex core (cloud or legacy Pi)."""
    ctx.ensure_object(dict)
    ctx.obj = {"timeout": timeout}


@cli.command()
@click.pass_context
def ping(ctx):
    """Test round-trip connectivity to Cortex Core."""
    bridge = _get_bridge(ctx)
    try:
        lines = bridge.send_and_wait("CMD:ping", timeout=5)
        if lines:
            click.echo(" | ".join(lines))
        else:
            click.echo("No response (timeout).", err=True)
            raise SystemExit(1)
    except ConnectionError as e:
        click.echo("Connection error: {}".format(e), err=True)
        raise SystemExit(1)


@cli.command()
@click.pass_context
def status(ctx):
    """Get Cortex Core status (uptime, storage, recording state)."""
    bridge = _get_bridge(ctx)
    click.echo(send_command(bridge, "status", timeout=5))


@cli.command()
@click.pass_context
def context(ctx):
    """Get full context for starting an AI session."""
    bridge = _get_bridge(ctx)
    click.echo(send_command(bridge, "get_context", timeout=20))


@cli.command()
@click.argument("content")
@click.option("--tags", "-t", default="", help="Comma-separated tags.")
@click.option("--project", "-p", default="", help="Project tag.")
@click.option("--type", "note_type", default="note",
              type=click.Choice(["note", "decision", "bug", "reminder", "idea", "todo", "context"]),
              help="Note type.")
@click.pass_context
def note(ctx, content, tags, project, note_type):
    """Store a note on Cortex Core."""
    bridge = _get_bridge(ctx)
    payload = {"content": content}
    if tags:
        payload["tags"] = tags
    if project:
        payload["project"] = project
    if note_type != "note":
        payload["type"] = note_type
    click.echo(send_command(bridge, "note", payload))


@cli.command()
@click.argument("program")
@click.option("--details", "-d", default="", help="Activity description.")
@click.option("--file", "file_path", default="", help="File path being worked on.")
@click.option("--project", "-p", default="", help="Project tag.")
@click.pass_context
def activity(ctx, program, details, file_path, project):
    """Log an activity (what program/file you're working on)."""
    bridge = _get_bridge(ctx)
    payload = {"program": program}
    if details:
        payload["details"] = details
    if file_path:
        payload["file_path"] = file_path
    if project:
        payload["project"] = project
    click.echo(send_command(bridge, "activity", payload))


@cli.command()
@click.argument("query_text")
@click.option("--source", "-s", default="web", help="Search source (google, github, etc.).")
@click.option("--url", "-u", default="", help="URL of the result.")
@click.option("--project", "-p", default="", help="Project tag.")
@click.pass_context
def search(ctx, query_text, source, url, project):
    """Log a search query."""
    bridge = _get_bridge(ctx)
    payload = {"query": query_text, "source": source}
    if url:
        payload["url"] = url
    if project:
        payload["project"] = project
    click.echo(send_command(bridge, "search", payload))


@cli.group()
def session():
    """Manage Cortex sessions (start/end)."""
    pass


@session.command("start")
@click.option("--platform", "ai_platform", default="claude", help="AI platform name.")
@click.pass_context
def session_start(ctx, ai_platform):
    """Start a new Cortex session."""
    bridge = _get_bridge(ctx)
    payload = {
        "ai_platform": ai_platform,
        "hostname": socket.gethostname(),
        "os_info": "{} {}".format(platform.system(), platform.release()),
    }
    click.echo(send_command(bridge, "session_start", payload))


@session.command("end")
@click.argument("session_id")
@click.argument("summary")
@click.option("--projects", default="", help="Comma-separated project tags.")
@click.pass_context
def session_end(ctx, session_id, summary, projects):
    """End a Cortex session with a summary."""
    bridge = _get_bridge(ctx)
    payload = {"session_id": session_id, "summary": summary}
    if projects:
        payload["projects"] = projects
    click.echo(send_command(bridge, "session_end", payload))


@cli.command()
@click.argument("table")
@click.option("--filters", "-f", default="", help='JSON filters, e.g. \'{"project":"cortex"}\'.')
@click.option("--limit", "-n", default=10, type=int, help="Max results.")
@click.option("--order-by", default="created_at DESC", help="SQL ORDER BY clause.")
@click.pass_context
def query(ctx, table, filters, limit, order_by):
    """Query the Cortex database."""
    bridge = _get_bridge(ctx)
    payload = {"table": table, "limit": limit, "order_by": order_by}
    if filters:
        try:
            payload["filters"] = json.loads(filters)
        except (json.JSONDecodeError, ValueError):
            click.echo("Error: --filters must be valid JSON.", err=True)
            raise SystemExit(1)
    click.echo(send_command(bridge, "query", payload, timeout=10))


@cli.group()
def files():
    """Browse and download files from Cortex Core (WiFi only)."""
    pass


@files.command("list")
@click.argument("category", type=click.Choice(["recordings", "notes", "logs", "uploads"]))
@click.pass_context
def files_list(ctx, category):
    """List files in a category on the Pi."""
    bridge = _get_bridge(ctx)
    if not hasattr(bridge, "list_files"):
        click.echo("Error: File operations require WiFi connection to Pi.", err=True)
        raise SystemExit(1)
    try:
        result = bridge.list_files(category)
        files = result.get("files", [])
        if not files:
            click.echo("No files in '{}'.".format(category))
            return
        for f in files:
            size = f.get("size", 0)
            if size > 1_048_576:
                size_str = "{:.1f}MB".format(size / 1_048_576)
            elif size > 1024:
                size_str = "{:.0f}KB".format(size / 1024)
            else:
                size_str = "{}B".format(size)
            click.echo("  {:>8s}  {}  {}".format(size_str, f.get("mtime", "")[:19], f["name"]))
        click.echo("\n{} files".format(len(files)))
    except Exception as e:
        click.echo("Error: {}".format(e), err=True)


@files.command("download")
@click.argument("category", type=click.Choice(["recordings", "notes", "logs", "uploads"]))
@click.argument("filename")
@click.option("--output", "-o", default=".", help="Local output directory.")
@click.pass_context
def files_download(ctx, category, filename, output):
    """Download a file from the Pi."""
    bridge = _get_bridge(ctx)
    if not hasattr(bridge, "download_file"):
        click.echo("Error: File operations require WiFi connection to Pi.", err=True)
        raise SystemExit(1)
    local_path = os.path.join(output, filename)
    try:
        click.echo("Downloading {} -> {}".format(filename, local_path))
        bridge.download_file(category, filename, local_path)
        size = os.path.getsize(local_path)
        click.echo("Done ({:.1f} KB)".format(size / 1024))
    except Exception as e:
        click.echo("Error: {}".format(e), err=True)


@files.command("upload")
@click.argument("local_file", type=click.Path(exists=True))
@click.pass_context
def files_upload(ctx, local_file):
    """Upload a file to the Pi's uploads directory."""
    bridge = _get_bridge(ctx)
    if not hasattr(bridge, "upload_file"):
        click.echo("Error: File operations require WiFi connection to Pi.", err=True)
        raise SystemExit(1)
    try:
        click.echo("Uploading {} ...".format(local_file))
        result = bridge.upload_file(local_file)
        click.echo("Done: {} ({} bytes)".format(result.get("filename"), result.get("size")))
    except Exception as e:
        click.echo("Error: {}".format(e), err=True)


@files.command("db")
@click.option("--output", "-o", default="cortex.db", help="Local output path.")
@click.pass_context
def files_db(ctx, output):
    """Download the cortex.db database from the Pi."""
    bridge = _get_bridge(ctx)
    if not hasattr(bridge, "download_db"):
        click.echo("Error: Database download requires WiFi connection to Pi.", err=True)
        raise SystemExit(1)
    try:
        click.echo("Downloading cortex.db -> {}".format(output))
        bridge.download_db(output)
        size = os.path.getsize(output)
        click.echo("Done ({:.1f} KB)".format(size / 1024))
    except Exception as e:
        click.echo("Error: {}".format(e), err=True)


# -- WiFi commands (headless Pi provisioning via BLE) --

@click.pass_context
def wifi_status(ctx):
    """Show the Pi's current WiFi connection status."""
    bridge = _get_bridge(ctx)
    click.echo(send_command(bridge, "wifi_status", timeout=10))



@click.pass_context
def wifi_scan(ctx):
    """Scan for available WiFi networks from the Pi."""
    click.echo("Scanning (this takes a few seconds)...")
    bridge = _get_bridge(ctx)
    result = send_command(bridge, "wifi_scan", timeout=20)
    click.echo(result)




# -- Setup command --

@cli.command()
@click.option("--target", type=click.Choice(["claude-code", "claude-desktop"]),
              default="claude-code", help="Which Claude app to configure.")
def setup(target):
    """Auto-configure Claude Code or Claude Desktop to use Cortex MCP.

    Uses the current Python interpreter with -m cortex_mcp.server, which
    avoids Windows PATH issues (pip Scripts dir often isn't on PATH).
    """
    import sys

    # Use the Python that has cortex-mcp installed (the one running this script)
    python_exe = str(Path(sys.executable).resolve())
    click.echo("Using Python: {}".format(python_exe))

    # Build the MCP server entry — portable across all platforms
    mcp_entry = {
        "command": python_exe,
        "args": ["-m", "cortex_mcp.server"],
    }

    if target == "claude-code":
        config_path = Path.home() / ".claude.json"

        # Read existing config or start fresh
        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                click.echo("Warning: existing {} is invalid JSON, creating new.".format(config_path))

        # Add/update mcpServers.cortex
        if "mcpServers" not in config:
            config["mcpServers"] = {}
        config["mcpServers"]["cortex"] = mcp_entry

        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        click.echo("Wrote Claude Code config to: {}".format(config_path))
        click.echo("\nRestart Claude Code to pick up the new MCP server.")

    elif target == "claude-desktop":
        if platform.system() == "Windows":
            config_path = Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
        elif platform.system() == "Darwin":
            config_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
        else:
            config_path = Path.home() / ".config" / "Claude" / "claude_desktop_config.json"

        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                click.echo("Warning: existing {} is invalid JSON, creating new.".format(config_path))

        if "mcpServers" not in config:
            config["mcpServers"] = {}
        config["mcpServers"]["cortex"] = mcp_entry

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        click.echo("Wrote Claude Desktop config to: {}".format(config_path))
        click.echo("\nRestart Claude Desktop to pick up the new MCP server.")

    click.echo("\nVerify with: python -m cortex_mcp ping")


def main():
    """Entry point for the cortex-cli console script."""
    cli()


if __name__ == "__main__":
    main()
