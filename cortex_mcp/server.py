"""Cortex MCP Bridge Server.

Connects to Cortex Link (ESP32) via USB serial and provides MCP tools
for AI agents to communicate with Cortex Core (Pi Zero 2 W) over BLE.

Data flow (with daemon):
    AI Agent -> MCP Server -> TCP -> cortex-daemon -> USB Serial -> ESP32 -> BLE -> Pi

Data flow (direct, fallback):
    AI Agent -> MCP Server -> USB Serial -> Cortex Link -> BLE -> Cortex Core

Run with:
    cortex-mcp          (installed entry point)
    python -m cortex_mcp.server  (direct invocation)
"""

import json
import os
import socket
import platform

from mcp.server.fastmcp import FastMCP

from cortex_mcp.bridge import SerialBridge, find_esp32_port, list_ports
from cortex_mcp.protocol import send_command


def _get_bridge():
    """Get the best available bridge: WiFi (preferred) -> daemon -> direct serial.

    WiFi is fastest (direct HTTP to Pi, no BLE relay). Falls back to
    BLE chain if Pi WiFi is unreachable.

    Environment variables:
        CORTEX_DIRECT=1    - Skip WiFi and daemon, use serial directly
        CORTEX_NO_WIFI=1   - Skip WiFi, use daemon/serial only
    """
    if os.environ.get("CORTEX_DIRECT"):
        return SerialBridge()

    # Try WiFi first (direct to Pi, bypasses ESP32 BLE chain)
    if not os.environ.get("CORTEX_NO_WIFI"):
        try:
            from cortex_mcp.wifi_bridge import WiFiBridge, is_pi_reachable
            if is_pi_reachable(timeout=1.0):
                return WiFiBridge()
        except Exception:
            pass

    # Try daemon (shared serial port)
    try:
        from cortex_mcp.daemon_client import DaemonBridge, is_daemon_running, ensure_daemon
        if is_daemon_running() or ensure_daemon():
            return DaemonBridge()
    except Exception:
        pass

    # Daemon unavailable, fall back to direct serial
    return SerialBridge()


# Lazy singleton bridge instance (deferred to avoid 1s WiFi timeout on import)
_bridge = None
# Optional serial connection for ESP32 icon notifications when using WiFi
_esp32_serial = None


def _reset_bridge():
    """Reset the bridge so the next call re-evaluates transport."""
    global _bridge, _esp32_serial
    _bridge = None
    # Close ESP32 serial notification port so daemon/serial bridge can use it
    if _esp32_serial is not None:
        try:
            _esp32_serial.close()
        except Exception:
            pass
        _esp32_serial = None


def _get_bridge_lazy():
    """Get or initialize the bridge singleton."""
    global _bridge
    if _bridge is None:
        _bridge = _get_bridge()
    return _bridge


def _notify_esp32(command):
    """Send a lightweight TOOL:<name> notification to the ESP32 for icon display.

    Only used when WiFi transport is active (ESP32 doesn't see commands).
    Fire-and-forget — failures are silently ignored.
    """
    global _esp32_serial
    try:
        if _esp32_serial is None:
            port = find_esp32_port()
            if not port:
                return
            import serial
            _esp32_serial = serial.Serial(port, 115200, timeout=0.1)
        _esp32_serial.write("TOOL:{}\n".format(command).encode("utf-8"))
    except Exception:
        _esp32_serial = None  # Reset on error

# MCP server
mcp = FastMCP(
    "Cortex Bridge",
    instructions=(
        "Cortex is a wearable AI memory system. A Pi Zero 2 W (Cortex Core) "
        "worn by the user stores notes, sessions, activities, searches, and "
        "files in a local SQLite database. An ESP32-S3 USB dongle (Cortex Link) "
        "provides BLE connectivity as a fallback.\n\n"

        "TRANSPORT (automatic, no action needed): "
        "WiFi HTTP (preferred, direct to Pi on port 8420) -> "
        "TCP daemon (shared serial, localhost:19750) -> "
        "direct USB serial to ESP32 -> BLE to Pi. "
        "The active transport is chosen automatically at startup. "
        "WiFi is 10-100x faster than BLE and supports file transfer. "
        "Use connection_info to check which transport is active.\n\n"

        "RECOMMENDED WORKFLOW:\n"
        "1. Call get_context first -- returns active projects, recent sessions, "
        "pending reminders, open bugs, recent files, and DB stats.\n"
        "2. Call session_start to register this conversation.\n"
        "3. Use tools as needed during the session.\n"
        "4. Call session_end with a summary before the conversation ends.\n\n"

        "CAPABILITIES:\n"
        "- Notes: send_note, note_update, notes_search\n"
        "- Projects: project_upsert, project_list\n"
        "- Sessions: session_start/session_end\n"
        "- Activities: log_activity, log_time\n"
        "- Searches: log_search\n"
        "- Database: query, upsert_row, delete_row, table_counts\n"
        "- Files: file_register, file_list, file_search, "
        "file_upload, file_download (WiFi only)\n"
        "- Audit: audit_projects, audit_notes, audit_data_quality, weekly_review\n"
        "- Pet: pet_chat, pet_feed, pet_clean, pet_rest, pet_analytics\n"
        "- WiFi: wifi_scan, wifi_status, wifi_config\n"
        "- Diagnostics: ping, get_status, connection_info\n\n"

        "WEEKLY REVIEW WORKFLOW:\n"
        "1. Call weekly_review for a full database health report.\n"
        "2. Review stale projects — update status with project_upsert or "
        "archive inactive ones.\n"
        "3. Triage untagged notes — use note_update to add tags and projects.\n"
        "4. Check data quality with audit_data_quality.\n"
        "5. Ask the user questions about projects and log decisions as notes.\n\n"

        "FILE OPERATIONS: Files on the Pi are organized by category: "
        "recordings, notes, logs, uploads. Use file_upload to send a file "
        "from this computer to the Pi over WiFi (auto-registers in DB). "
        "Use file_download to retrieve files. file_register records metadata "
        "for files already on the Pi. File transfer requires WiFi transport."
    ),
)


@mcp.tool()
def ping() -> str:
    """Ping the Pi Zero to test round-trip connectivity.

    Sends CMD:ping through the ESP32 BLE bridge and waits for CMD:pong.
    Use this to verify the full chain: Computer -> ESP32 -> BLE -> Pi.
    """
    try:
        return send_command(_get_bridge_lazy(), "ping", timeout=5)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def get_status() -> str:
    """Get the Pi Zero's current status.

    Returns uptime, connection info, storage stats, and recording state.
    """
    try:
        return send_command(_get_bridge_lazy(), "status", timeout=5)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def send_note(content: str, tags: str = "", project: str = "", note_type: str = "note") -> str:
    """Send a text note to the Pi Zero for storage.

    Notes are timestamped and stored on the Pi's SD card for future analysis.
    Notes of any length are supported -- the transport handles chunking automatically.

    Args:
        content: The note text to store.
        tags: Optional comma-separated tags for categorization
              (e.g. "idea,project,urgent").
        project: Optional project tag (e.g. "cortex", "bewell").
        note_type: Note type: note, decision, bug, reminder, idea, todo, context.
    """
    try:
        payload = {"content": content}
        if tags:
            payload["tags"] = tags
        if project:
            payload["project"] = project
        if note_type and note_type != "note":
            payload["type"] = note_type
        return send_command(_get_bridge_lazy(), "note", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def log_activity(program: str, details: str = "", file_path: str = "", project: str = "") -> str:
    """Log what the user is currently working on.

    Records the program, optional file path, and details to the Pi for
    building an activity timeline.

    Args:
        program: Program name (e.g. "VS Code", "Chrome", "Terminal").
        details: Optional description of the activity.
        file_path: Optional file path being worked on.
        project: Optional project tag.
    """
    try:
        payload = {"program": program}
        if details:
            payload["details"] = details
        if file_path:
            payload["file_path"] = file_path
        if project:
            payload["project"] = project
        return send_command(_get_bridge_lazy(), "activity", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def log_time(
    project: str,
    duration_minutes: int,
    description: str = "",
    activity_type: str = "development",
    date: str = "",
    project_name: str = "",
    org_tag: str = "",
) -> str:
    """Log a time entry for work done on a project.

    Creates a time entry and automatically creates the project if it doesn't
    exist yet. Use this to log work after a session — estimate duration from
    the conversation context.

    Args:
        project: Project tag (e.g. "cortex-desktop", "bewell"). Auto-creates if missing.
        duration_minutes: Estimated duration in minutes.
        description: Brief description of the work done.
        activity_type: Type of work: development, bugfix, research, documentation,
                      devops, meeting, design, testing, planning.
        date: Approximate date/time as ISO string (e.g. "2026-04-02T14:00:00").
              Defaults to now if omitted.
        project_name: Friendly name for new projects (e.g. "Cortex Desktop").
                     Defaults to project tag if omitted.
        org_tag: Optional organization tag.
    """
    try:
        payload = {
            "project": project,
            "duration_minutes": duration_minutes,
        }
        if description:
            payload["description"] = description
        if activity_type and activity_type != "development":
            payload["activity_type"] = activity_type
        if date:
            payload["date"] = date
        if project_name:
            payload["project_name"] = project_name
        if org_tag:
            payload["org_tag"] = org_tag
        return send_command(_get_bridge_lazy(), "log_time", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def log_search(query: str, url: str = "", source: str = "web", project: str = "") -> str:
    """Log a web search or research query.

    Records searches for building a research history on the Pi.

    Args:
        query: The search query text.
        url: Optional URL of the search or result page.
        source: Search engine or source (e.g. "google", "github", "stackoverflow").
        project: Optional project tag.
    """
    try:
        payload = {"query": query, "source": source}
        if url:
            payload["url"] = url
        if project:
            payload["project"] = project
        return send_command(_get_bridge_lazy(), "search", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def session_start(ai_platform: str = "claude") -> str:
    """Start a new Cortex session.

    Call this at the beginning of a conversation to register the session
    with Cortex Core. Returns a session_id for use in subsequent calls.

    Args:
        ai_platform: The AI platform name (e.g. "claude", "chatgpt").
    """
    try:
        payload = {
            "ai_platform": ai_platform,
            "hostname": socket.gethostname(),
            "os_info": "{} {}".format(platform.system(), platform.release()),
        }
        return send_command(_get_bridge_lazy(), "session_start", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def session_end(session_id: str, summary: str, projects: str = "") -> str:
    """End a Cortex session.

    Call this before a conversation ends to record what was accomplished.

    Args:
        session_id: The session ID from session_start.
        summary: Brief summary of what was accomplished in this session.
        projects: Comma-separated project tags that were touched.
    """
    try:
        payload = {
            "session_id": session_id,
            "summary": summary,
        }
        if projects:
            payload["projects"] = projects
        return send_command(_get_bridge_lazy(), "session_end", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def get_context() -> str:
    """Get full context for starting an informed AI session.

    Returns active projects, recent sessions, pending reminders,
    recent decisions, open bugs, and computer info. Call this at the
    start of every conversation to understand what the user is working on.
    """
    try:
        return send_command(_get_bridge_lazy(), "get_context", timeout=20)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def query(table: str, filters: str = "", limit: int = 10, order_by: str = "created_at DESC") -> str:
    """Query the Cortex database on the Pi.

    Generic query interface for retrieving stored data.

    Args:
        table: Table to query (notes, activities, searches, sessions, projects, computers, people, files).
        filters: JSON string of filters, e.g. '{"project":"cortex","type":"bug"}'.
        limit: Max results to return (default 10).
        order_by: SQL ORDER BY clause (default "created_at DESC").
    """
    try:
        payload = {"table": table, "limit": limit, "order_by": order_by}
        if filters:
            try:
                payload["filters"] = json.loads(filters)
            except (json.JSONDecodeError, ValueError):
                return "Error: 'filters' must be valid JSON (e.g. '{\"project\":\"cortex\"}')"
        return send_command(_get_bridge_lazy(), "query", payload, timeout=10)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def register_computer() -> str:
    """Register this computer with Cortex Core.

    Auto-detects hostname, OS, platform, and Python version.
    Useful for tracking which machines the user works on.
    """
    try:
        payload = {
            "hostname": socket.gethostname(),
            "os_info": "{} {} {}".format(
                platform.system(), platform.release(), platform.version()
            ),
            "platform": platform.machine(),
        }
        return send_command(_get_bridge_lazy(), "computer_reg", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_register(filename: str, category: str = "uploads", description: str = "",
                  tags: str = "", project: str = "", mime_type: str = "",
                  size_bytes: int = 0) -> str:
    """Register a file in the Cortex database for sharing and discovery.

    Records file metadata so AI agents can find and serve files by context.
    The file must already exist on the Pi (in the appropriate category directory).

    To transfer a file FROM this computer to the Pi, use file_upload instead.
    file_upload auto-registers the file in the DB after upload.

    Args:
        filename: Name of the file on the Pi.
        category: File category: recordings, notes, logs, uploads.
        description: Human-readable description of the file contents.
        tags: Comma-separated tags for categorization.
        project: Project tag this file belongs to.
        mime_type: MIME type (auto-detected if empty).
        size_bytes: File size in bytes.
    """
    try:
        payload = {"filename": filename, "category": category}
        if description:
            payload["description"] = description
        if tags:
            payload["tags"] = tags
        if project:
            payload["project"] = project
        if mime_type:
            payload["mime_type"] = mime_type
        if size_bytes:
            payload["size_bytes"] = size_bytes
        return send_command(_get_bridge_lazy(), "file_register", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_list(category: str = "", project: str = "", limit: int = 50) -> str:
    """List files registered in the Cortex database.

    Returns file metadata including name, description, tags, and download info.

    Args:
        category: Filter by category (recordings, notes, logs, uploads). Empty for all.
        project: Filter by project tag. Empty for all.
        limit: Max results (default 50).
    """
    try:
        payload = {"limit": limit}
        if category:
            payload["category"] = category
        if project:
            payload["project"] = project
        return send_command(_get_bridge_lazy(), "file_list", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_search(query: str, limit: int = 20) -> str:
    """Search for files by name, description, or tags.

    Searches across filename, description, and tags fields.

    Args:
        query: Search text to match against file metadata.
        limit: Max results (default 20).
    """
    try:
        payload = {"query": query, "limit": limit}
        return send_command(_get_bridge_lazy(), "file_search", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_upload(local_path: str, remote_name: str = "", description: str = "",
                tags: str = "", project: str = "") -> str:
    """Upload a file from this computer to the Pi Zero over WiFi.

    Transfers the file contents via HTTP and auto-registers it in the Cortex
    database with metadata. Only works when WiFi transport is active (not BLE).

    Use connection_info to verify WiFi is connected before uploading.

    Args:
        local_path: Absolute path to the file on this computer.
        remote_name: Filename on the Pi (defaults to local filename).
        description: Human-readable description of the file.
        tags: Comma-separated tags for categorization.
        project: Project tag this file belongs to.
    """
    try:
        bridge = _get_bridge_lazy()
        if not hasattr(bridge, "upload_file"):
            return ("Error: file_upload requires WiFi transport. "
                    "Current transport does not support file transfer. "
                    "Use connection_info to check WiFi status.")
        if not os.path.isfile(local_path):
            return "Error: file not found: {}".format(local_path)
        result = bridge.upload_file(
            local_path,
            remote_name=remote_name or None,
            description=description,
            tags=tags,
            project=project,
        )
        return "Uploaded: {} ({} bytes, file_id={})".format(
            result.get("filename"), result.get("size"), result.get("file_id"))
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def file_download(category: str, filename: str, local_path: str = "") -> str:
    """Download a file from the Pi Zero to this computer over WiFi.

    Retrieves file contents via HTTP. Only works when WiFi transport is active.

    Args:
        category: File category on the Pi: recordings, notes, logs, uploads.
        filename: Name of the file to download.
        local_path: Local destination path (defaults to current directory + filename).
    """
    try:
        bridge = _get_bridge_lazy()
        if not hasattr(bridge, "download_file"):
            return ("Error: file_download requires WiFi transport. "
                    "Current transport does not support file transfer. "
                    "Use connection_info to check WiFi status.")
        dest = local_path or os.path.join(".", filename)
        bridge.download_file(category, filename, dest)
        size = os.path.getsize(dest)
        return "Downloaded: {} -> {} ({} bytes)".format(filename, dest, size)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def wifi_scan() -> str:
    """Scan for available WiFi networks near the Pi Zero.

    Returns a list of networks with SSID, signal strength, and security type.
    Uses nmcli (preferred) or iwlist as fallback. Takes a few seconds for rescan.
    """
    try:
        return send_command(_get_bridge_lazy(), "wifi_scan", timeout=20)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def wifi_status() -> str:
    """Get the Pi Zero's current WiFi connection status.

    Returns current IP address, connected SSID, signal strength, and hostname.
    """
    try:
        return send_command(_get_bridge_lazy(), "wifi_status", timeout=5)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def wifi_config(ssid: str, password: str = "") -> str:
    """Connect the Pi Zero to a WiFi network (headless provisioning).

    Configures and connects to the specified network using nmcli or wpa_cli.
    Particularly useful when provisioning over BLE -- the Pi has no keyboard
    or screen for manual WiFi setup.

    Args:
        ssid: The WiFi network name to connect to.
        password: Network password (omit for open networks).
    """
    try:
        payload = {"ssid": ssid}
        if password:
            payload["password"] = password
        return send_command(_get_bridge_lazy(), "wifi_config", payload, timeout=40)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def shell_exec(command: str, timeout: int = 30, cwd: str = "") -> str:
    """Execute a shell command on the Pi Zero and return the output.

    Runs the command on the Pi via the active transport (WiFi or BLE).
    Useful for deploying, debugging, and managing the Pi remotely.

    Args:
        command: The shell command to execute (e.g. "ls -la", "systemctl status cortex-core").
        timeout: Max seconds to wait for the command (default 30, max 120).
        cwd: Working directory on the Pi (defaults to home directory).
    """
    try:
        payload = {"command": command, "timeout": min(timeout, 120)}
        if cwd:
            payload["cwd"] = cwd
        return send_command(
            _get_bridge_lazy(), "shell_exec", payload,
            timeout=min(timeout + 5, 125),
        )
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def pet_analytics(days: int = 7) -> str:
    """Get pet analytics: mood trends, interaction frequency, stage progress.

    Returns daily mood averages, mood distribution, inference performance
    stats, and stage progression history.

    Args:
        days: Number of days to analyze (default 7, max 90).
    """
    try:
        payload = {"days": min(days, 90)}
        return send_command(_get_bridge_lazy(), "pet_analytics", payload, timeout=10)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def pet_chat(message: str, timeout: int = 120) -> str:
    """Chat with the Cortex pet using its on-device LLM.

    Sends a message to the pet and waits for its response. The pet runs a
    small language model (Qwen 0.8B) on the Orange Pi, so responses take
    10-60 seconds. Be patient!

    The pet has its own personality shaped by its mood, hunger, energy, and
    training history. Responses reflect its current emotional state.

    Args:
        message: What you want to say to the pet.
        timeout: Max seconds to wait for a response (default 120).
    """
    import time as _time

    try:
        bridge = _get_bridge_lazy()

        # Step 1: Send pet_ask — returns ACK with interaction ID
        ack_raw = send_command(bridge, "pet_ask", {"prompt": message})
        if "Error" in ack_raw or "timeout" in ack_raw.lower():
            return "Failed to send message to pet: {}".format(ack_raw)

        # Parse interaction ID from ACK
        interaction_id = 0
        if "ACK" in ack_raw:
            try:
                interaction_id = int(ack_raw.split(":")[-1].strip().rstrip(")"))
            except (ValueError, IndexError):
                pass

        since_id = max(interaction_id - 1, 0)

        # Step 2: Poll for the pet's response
        deadline = _time.monotonic() + min(timeout, 180)
        while _time.monotonic() < deadline:
            _time.sleep(3.0)
            poll_raw = send_command(
                bridge, "pet_response", {"since_id": since_id}, timeout=10
            )
            if "Error" in poll_raw or "timeout" in poll_raw.lower():
                continue

            # Try to parse the JSON response list
            try:
                import json as _json
                items = _json.loads(poll_raw)
                if isinstance(items, list):
                    # Find our specific response
                    for item in items:
                        if item.get("id") == interaction_id:
                            return item.get("response", "(empty response)")
                    # Or return the latest if available
                    if items:
                        latest = items[-1]
                        return latest.get("response", "(empty response)")
            except (ValueError, _json.JSONDecodeError):
                # Response might be wrapped differently
                if "RSP:pet_response:" in poll_raw:
                    data_part = poll_raw.split("RSP:pet_response:", 1)[-1]
                    try:
                        items = _json.loads(data_part)
                        if isinstance(items, list) and items:
                            for item in items:
                                if item.get("id") == interaction_id:
                                    return item.get("response", "(empty response)")
                            return items[-1].get("response", "(empty response)")
                    except Exception:
                        pass

        return "(The pet didn't respond in time — it may be sleeping or in a coma.)"
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def pet_feed(feed_type: str = "chat_snack") -> str:
    """Feed the Cortex pet to restore hunger.

    Args:
        feed_type: Type of food — "chat_snack" (+15%), "data_meal" (+25%),
                   or "training_feast" (+40%).
    """
    try:
        bridge = _get_bridge_lazy()
        return send_command(bridge, "pet_feed", {"type": feed_type})
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def pet_clean() -> str:
    """Clean the Cortex pet to restore cleanliness.

    Performs a quick clean, discarding no specific interactions.
    """
    try:
        bridge = _get_bridge_lazy()
        return send_command(bridge, "pet_clean", {})
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def pet_rest() -> str:
    """Let the Cortex pet rest to restore energy (+10%)."""
    try:
        bridge = _get_bridge_lazy()
        return send_command(bridge, "pet_rest")
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def send_message(message: str) -> str:
    """Send an arbitrary message to the Pi Zero through the bridge.

    Use for custom commands or data not covered by other tools.
    Messages are newline-delimited UTF-8, max 512 bytes.

    Args:
        message: The message to send.
    """
    try:
        lines = _get_bridge_lazy().send_and_wait(message, timeout=5)
        if lines:
            return "\n".join(lines)
        return "Sent (no response)."
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def read_responses() -> str:
    """Read any pending messages from the Pi Zero.

    Returns buffered messages that arrived without a preceding request.
    Useful for checking unsolicited data or async responses.
    """
    try:
        bridge = _get_bridge_lazy()
        bridge._ensure_connected()
        lines = bridge.read_pending()
        if lines:
            return "\n".join(lines)
        return "No pending messages."
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def connection_info() -> str:
    """Show current connection status and available ports.

    Lists detected serial ports, WiFi status, and the active connection details.
    Transport hierarchy: WiFi HTTP (fastest) -> TCP daemon -> direct serial (BLE).
    File upload/download only work over WiFi transport.
    """
    try:
        bridge = _get_bridge_lazy()
        info = ""

        # WiFi status
        try:
            from cortex_mcp.wifi_bridge import is_pi_reachable, get_pi_host, get_pi_port
            host = get_pi_host()
            port = get_pi_port()
            if is_pi_reachable(timeout=1.0):
                info += "WiFi: connected (http://{}:{})\n".format(host, port)
            else:
                info += "WiFi: unreachable ({}:{})\n".format(host, port)
        except Exception:
            info += "WiFi: not available\n"

        info += "Active transport: {}\n\n".format(bridge.port_name)

        # Serial ports
        port_list = list_ports()
        info += "Available ports:\n"
        if port_list:
            info += "\n".join("  " + p for p in port_list)
        else:
            info += "  (none detected)"

        info += "\n\n"

        if bridge.is_connected:
            info += "Connected: {}\n".format(bridge.port_name)
            if bridge.baud_rate:
                info += "Baud: {}\n".format(bridge.baud_rate)
            info += "Buffered messages: {}".format(bridge.buffered_count)
        else:
            info += "Status: Not connected"
            auto = find_esp32_port()
            if auto:
                info += "\nAuto-detected ESP32: {}".format(auto)

        return info
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Project Management Tools
# ===================================================================


@mcp.tool()
def project_upsert(
    tag: str,
    name: str = "",
    status: str = "active",
    priority: int = 3,
    description: str = "",
    category: str = "",
    org_tag: str = "",
    github_url: str = "",
    collaborators: str = "",
) -> str:
    """Create or update a project in the Cortex database.

    If the project tag already exists, its fields are updated.
    If it doesn't exist, a new project is created.

    Args:
        tag: Unique project identifier (e.g. "cortex-desktop", "bewell"). Required.
        name: Human-friendly name (e.g. "Cortex Desktop"). Defaults to tag.
        status: Project status: active, archived, paused, completed.
        priority: Priority 1-5 (1 = highest).
        description: What the project is about.
        category: Project category (e.g. "ai", "web", "hardware").
        org_tag: Organization this project belongs to.
        github_url: GitHub repository URL.
        collaborators: Comma-separated list of collaborator names/IDs.
    """
    try:
        payload = {"tag": tag}
        if name:
            payload["name"] = name
        if status:
            payload["status"] = status
        if priority != 3:
            payload["priority"] = priority
        if description:
            payload["description"] = description
        if category:
            payload["category"] = category
        if org_tag:
            payload["org_tag"] = org_tag
        if github_url:
            payload["github_url"] = github_url
        if collaborators:
            payload["collaborators"] = collaborators
        _notify_esp32("project_upsert")
        return send_command(_get_bridge_lazy(), "project_upsert", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def project_list(status: str = "", category: str = "", limit: int = 50) -> str:
    """List all projects, optionally filtered by status or category.

    Args:
        status: Filter by status (active, archived, paused, completed). Empty for all.
        category: Filter by category. Empty for all.
        limit: Max results (default 50).
    """
    try:
        payload = {
            "table": "projects",
            "limit": limit,
            "order_by": "last_touched DESC",
        }
        filters = {}
        if status:
            filters["status"] = status
        if category:
            filters["category"] = category
        if filters:
            payload["filters"] = filters
        return send_command(_get_bridge_lazy(), "query", payload, timeout=10)
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Note Management Tools
# ===================================================================


@mcp.tool()
def note_update(note_id: int, tags: str = "", project: str = "", note_type: str = "") -> str:
    """Update an existing note's tags, project, or type.

    Use this to triage and categorize notes during review.

    Args:
        note_id: The note ID to update. Required.
        tags: New comma-separated tags (replaces existing).
        project: New project tag to assign.
        note_type: New note type: note, decision, bug, reminder, idea, todo, context.
    """
    try:
        row_data = {"id": note_id}
        if tags:
            row_data["tags"] = tags
        if project:
            row_data["project"] = project
        if note_type:
            row_data["note_type"] = note_type
        payload = {"table": "notes", "data": row_data}
        _notify_esp32("upsert")
        return send_command(_get_bridge_lazy(), "upsert", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def notes_search(search_text: str, project: str = "", note_type: str = "", limit: int = 30) -> str:
    """Search notes by content text, with optional project/type filters.

    Performs a case-insensitive substring search across note content.
    Also supports filtering by project tag and note type.

    Args:
        search_text: Text to search for in note content. Required.
        project: Filter to notes in this project only.
        note_type: Filter by type: note, decision, bug, reminder, idea, todo, context.
        limit: Max results (default 30).
    """
    try:
        # The Pi query command only supports exact = filters, so we fetch
        # a larger set and filter client-side for content matching.
        payload = {
            "table": "notes",
            "limit": 100,
            "order_by": "created_at DESC",
        }
        filters = {}
        if project:
            filters["project"] = project
        if note_type:
            filters["note_type"] = note_type
        if filters:
            payload["filters"] = filters
        raw = send_command(_get_bridge_lazy(), "query", payload, timeout=10)

        # Parse response and filter by content
        if raw.startswith("RSP:query:"):
            data = json.loads(raw[len("RSP:query:"):])
        elif raw.startswith("["):
            data = json.loads(raw)
        else:
            return raw  # Error or unexpected format

        needle = search_text.lower()
        matches = [
            row for row in data
            if needle in (row.get("content") or "").lower()
        ][:limit]
        return json.dumps(matches, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Generic CRUD Tools
# ===================================================================


@mcp.tool()
def upsert_row(table: str, data: str) -> str:
    """Insert or update a row in any Cortex database table.

    If the data includes an 'id' (or primary key) that already exists,
    the row is updated. Otherwise a new row is inserted.

    Args:
        table: Table name (notes, projects, activities, searches, sessions,
               computers, people, files, organizations, time_entries).
        data: JSON string of column-value pairs, e.g. '{"tag":"my-proj","name":"My Project"}'.
    """
    try:
        try:
            row_data = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            return "Error: 'data' must be valid JSON (e.g. '{\"tag\":\"my-proj\"}')"
        payload = {"table": table, "data": row_data}
        _notify_esp32("upsert")
        return send_command(_get_bridge_lazy(), "upsert", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def delete_row(table: str, row_id: int) -> str:
    """Delete a row from a Cortex database table by its ID.

    Args:
        table: Table name (notes, activities, searches, sessions, files, etc.).
        row_id: The row ID to delete.
    """
    try:
        payload = {"table": table, "id": row_id}
        _notify_esp32("delete")
        return send_command(_get_bridge_lazy(), "delete", payload)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def table_counts() -> str:
    """Get row counts for all tables in the Cortex database.

    Returns a summary of how many rows each table contains.
    Useful for a quick health check or data overview.
    """
    try:
        return send_command(_get_bridge_lazy(), "table_counts", timeout=10)
    except Exception as e:
        return "Error: {}".format(e)


# ===================================================================
# Audit & Upkeep Tools
# ===================================================================


def _query_table(table, filters=None, limit=100, order_by="created_at DESC"):
    """Internal helper: query a table and return parsed list of dicts."""
    payload = {"table": table, "limit": limit, "order_by": order_by}
    if filters:
        payload["filters"] = filters
    raw = send_command(_get_bridge_lazy(), "query", payload, timeout=15)
    if raw.startswith("RSP:query:"):
        return json.loads(raw[len("RSP:query:"):])
    elif raw.startswith("["):
        return json.loads(raw)
    return []


@mcp.tool()
def audit_projects(stale_days: int = 30) -> str:
    """Audit all projects for staleness, missing data, and activity levels.

    Reviews each project and flags issues:
    - Stale: no activity (notes, sessions, time entries) in N+ days
    - Missing description
    - No time logged
    - No notes linked

    Args:
        stale_days: Number of days without activity to consider a project stale (default 30).
    """
    try:
        from datetime import datetime, timedelta

        projects = _query_table("projects", limit=100, order_by="last_touched DESC")
        notes = _query_table("notes", limit=100)
        time_entries = _query_table("time_entries", limit=100, order_by="created_at DESC")

        # Index notes and time by project
        notes_by_project = {}
        for n in notes:
            p = n.get("project", "")
            if p:
                notes_by_project.setdefault(p, []).append(n)

        time_by_project = {}
        for t in time_entries:
            p = t.get("project_tag", "")
            if p:
                time_by_project.setdefault(p, []).append(t)

        cutoff = (datetime.utcnow() - timedelta(days=stale_days)).isoformat()
        report = []

        for proj in projects:
            tag = proj.get("tag", "")
            issues = []
            last_touched = proj.get("last_touched") or proj.get("created_at") or ""

            if last_touched and last_touched < cutoff:
                issues.append("stale (no activity in {}+ days)".format(stale_days))
            if not proj.get("description"):
                issues.append("missing description")

            note_count = len(notes_by_project.get(tag, []))
            time_count = len(time_by_project.get(tag, []))
            total_hours = proj.get("total_hours", 0)

            if time_count == 0:
                issues.append("no time logged")
            if note_count == 0:
                issues.append("no notes linked")

            report.append({
                "tag": tag,
                "name": proj.get("name", ""),
                "status": proj.get("status", ""),
                "last_touched": last_touched,
                "total_hours": total_hours,
                "note_count": note_count,
                "time_entry_count": time_count,
                "issues": issues,
            })

        # Sort: projects with issues first, then by last_touched
        report.sort(key=lambda x: (len(x["issues"]) == 0, x.get("last_touched") or ""))

        summary = {
            "total_projects": len(projects),
            "projects_with_issues": sum(1 for r in report if r["issues"]),
            "stale_projects": sum(1 for r in report if any("stale" in i for i in r["issues"])),
            "projects": report,
        }
        return json.dumps(summary, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def audit_notes(limit: int = 50) -> str:
    """Find notes that need triage — untagged, uncategorized, or unlinked.

    Returns notes missing tags, project assignment, or using the default 'note' type.
    Use note_update to fix them.

    Args:
        limit: Max notes to return (default 50).
    """
    try:
        notes = _query_table("notes", limit=100, order_by="created_at DESC")

        untagged = []
        no_project = []
        default_type = []

        for n in notes:
            note_id = n.get("id")
            preview = (n.get("content") or "")[:100]
            entry = {
                "id": note_id,
                "preview": preview,
                "created_at": n.get("created_at", ""),
                "tags": n.get("tags", ""),
                "project": n.get("project", ""),
                "note_type": n.get("note_type", ""),
            }
            if not n.get("tags"):
                untagged.append(entry)
            if not n.get("project"):
                no_project.append(entry)
            if n.get("note_type", "note") == "note":
                default_type.append(entry)

        result = {
            "total_notes_checked": len(notes),
            "untagged_count": len(untagged),
            "no_project_count": len(no_project),
            "default_type_count": len(default_type),
            "untagged": untagged[:limit],
            "no_project": no_project[:limit],
            "default_type": default_type[:limit],
        }
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def audit_data_quality() -> str:
    """Run a data quality and health check on the Cortex database.

    Checks:
    - Table row counts
    - Sessions with no summary (incomplete)
    - Projects with no activity
    - Orphaned time entries (project doesn't exist)
    - Overall database health summary
    """
    try:
        # Get table counts
        counts_raw = send_command(_get_bridge_lazy(), "table_counts", timeout=10)
        if counts_raw.startswith("RSP:table_counts:"):
            counts = json.loads(counts_raw[len("RSP:table_counts:"):])
        elif counts_raw.startswith("{"):
            counts = json.loads(counts_raw)
        else:
            counts = {}

        # Check for incomplete sessions (no summary)
        sessions = _query_table("sessions", limit=50, order_by="started_at DESC")
        incomplete_sessions = [
            {"id": s.get("id"), "started_at": s.get("started_at"), "ai_platform": s.get("ai_platform")}
            for s in sessions
            if not s.get("summary") and not s.get("ended_at")
        ]

        # Check projects with no recent activity
        projects = _query_table("projects", limit=100, order_by="last_touched DESC")
        project_tags = {p.get("tag") for p in projects}

        # Check time entries referencing non-existent projects
        time_entries = _query_table("time_entries", limit=100, order_by="created_at DESC")
        orphaned_time = [
            {"id": t.get("id"), "project_tag": t.get("project_tag"), "description": t.get("description")}
            for t in time_entries
            if t.get("project_tag") and t.get("project_tag") not in project_tags
        ]

        issues = []
        if incomplete_sessions:
            issues.append("{} incomplete sessions (no summary/end)".format(len(incomplete_sessions)))
        if orphaned_time:
            issues.append("{} time entries reference non-existent projects".format(len(orphaned_time)))
        if counts.get("notes", 0) == 0:
            issues.append("No notes in database")
        if counts.get("projects", 0) == 0:
            issues.append("No projects in database")

        result = {
            "table_counts": counts,
            "issues_found": len(issues),
            "issues": issues,
            "incomplete_sessions": incomplete_sessions[:10],
            "orphaned_time_entries": orphaned_time[:10],
            "health": "good" if len(issues) == 0 else "needs attention",
        }
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


@mcp.tool()
def weekly_review() -> str:
    """Run a comprehensive weekly review of the Cortex database.

    Combines table counts, project audit, note triage, and recent session
    summary into a single structured report. Use this at the start of a
    weekly upkeep session to understand what needs attention.

    The report includes:
    - Database overview (row counts)
    - Project health (stale, missing data)
    - Notes needing triage (untagged, uncategorized)
    - Recent sessions summary
    - Actionable recommendations
    """
    try:
        from datetime import datetime, timedelta

        # 1. Table counts
        counts_raw = send_command(_get_bridge_lazy(), "table_counts", timeout=10)
        if counts_raw.startswith("RSP:table_counts:"):
            counts = json.loads(counts_raw[len("RSP:table_counts:"):])
        elif counts_raw.startswith("{"):
            counts = json.loads(counts_raw)
        else:
            counts = {}

        # 2. Projects
        projects = _query_table("projects", limit=100, order_by="last_touched DESC")
        cutoff_30d = (datetime.utcnow() - timedelta(days=30)).isoformat()
        cutoff_7d = (datetime.utcnow() - timedelta(days=7)).isoformat()

        active_projects = [p for p in projects if p.get("status") == "active"]
        stale_projects = [
            p.get("tag") for p in active_projects
            if (p.get("last_touched") or "") < cutoff_30d
        ]

        # 3. Notes triage
        notes = _query_table("notes", limit=100, order_by="created_at DESC")
        recent_notes = [n for n in notes if (n.get("created_at") or "") >= cutoff_7d]
        untagged_notes = [n for n in notes if not n.get("tags")]
        no_project_notes = [n for n in notes if not n.get("project")]

        # 4. Recent sessions
        sessions = _query_table("sessions", limit=10, order_by="started_at DESC")
        recent_sessions = []
        for s in sessions:
            recent_sessions.append({
                "id": s.get("id"),
                "started_at": s.get("started_at"),
                "summary": (s.get("summary") or "(no summary)")[:120],
                "projects": s.get("projects", ""),
            })

        # 5. Time entries this week
        time_entries = _query_table("time_entries", limit=100, order_by="created_at DESC")
        weekly_time = [t for t in time_entries if (t.get("created_at") or "") >= cutoff_7d]
        weekly_hours = sum(t.get("duration_minutes", 0) for t in weekly_time) / 60.0

        # 6. Build recommendations
        recommendations = []
        if stale_projects:
            recommendations.append(
                "Review {} stale projects: {}".format(
                    len(stale_projects), ", ".join(stale_projects[:5])
                )
            )
        if untagged_notes:
            recommendations.append(
                "Triage {} untagged notes (use note_update to add tags)".format(len(untagged_notes))
            )
        if no_project_notes:
            recommendations.append(
                "Link {} notes to projects (use note_update)".format(len(no_project_notes))
            )
        incomplete = [s for s in sessions if not s.get("summary") and not s.get("ended_at")]
        if incomplete:
            recommendations.append(
                "Close {} incomplete sessions".format(len(incomplete))
            )
        if not recommendations:
            recommendations.append("Everything looks good! Database is well-maintained.")

        report = {
            "report_date": datetime.utcnow().isoformat()[:10],
            "database_overview": counts,
            "projects": {
                "total": len(projects),
                "active": len(active_projects),
                "stale_30d": stale_projects,
            },
            "notes": {
                "total": counts.get("notes", len(notes)),
                "added_this_week": len(recent_notes),
                "untagged": len(untagged_notes),
                "no_project": len(no_project_notes),
            },
            "sessions": {
                "recent": recent_sessions[:5],
            },
            "time_tracking": {
                "hours_this_week": round(weekly_hours, 1),
                "entries_this_week": len(weekly_time),
            },
            "recommendations": recommendations,
        }
        return json.dumps(report, indent=2, default=str)
    except Exception as e:
        return "Error: {}".format(e)


def main():
    """Entry point for the cortex-mcp console script."""
    mcp.run()


if __name__ == "__main__":
    main()
