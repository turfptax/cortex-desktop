"""Cortex protocol helpers.

Protocol format:
    Commands:  CMD:<command>:<json_payload>
    Responses: RSP:<command>:<json_payload>
    Acks:      ACK:<command>:<id>
    Errors:    ERR:<command>:<message>
"""

import json


def build_command(command, payload=None):
    """Build a Cortex protocol command string."""
    if payload is None:
        return "CMD:{}".format(command)
    if isinstance(payload, dict):
        payload = json.dumps(payload)
    return "CMD:{}:{}".format(command, payload)


def parse_response(lines):
    """Parse Cortex protocol response lines into a structured result.

    Returns a dict with 'type' (ACK/RSP/ERR/raw), 'command', 'data', and 'raw',
    or None if no lines.
    """
    if not lines:
        return None

    raw = "\n".join(lines)

    for line in lines:
        if line.startswith("ACK:"):
            parts = line.split(":", 2)
            return {
                "type": "ACK",
                "command": parts[1] if len(parts) > 1 else "",
                "data": parts[2] if len(parts) > 2 else "",
                "raw": raw,
            }
        if line.startswith("RSP:"):
            parts = line.split(":", 2)
            data_str = parts[2] if len(parts) > 2 else ""
            try:
                data = json.loads(data_str)
            except (json.JSONDecodeError, ValueError):
                data = data_str
            return {
                "type": "RSP",
                "command": parts[1] if len(parts) > 1 else "",
                "data": data,
                "raw": raw,
            }
        if line.startswith("ERR:"):
            parts = line.split(":", 2)
            return {
                "type": "ERR",
                "command": parts[1] if len(parts) > 1 else "",
                "data": parts[2] if len(parts) > 2 else "",
                "raw": raw,
            }

    return {"type": "raw", "command": "", "data": raw, "raw": raw}


def send_command(bridge, command, payload=None, timeout=None):
    """Send a Cortex command via the bridge and return a formatted result string.

    If the current transport fails with a connection error, resets the bridge
    so the next call re-evaluates the transport hierarchy (WiFi -> daemon -> serial).

    When using WiFi transport, also sends a TOOL:<command> notification to the
    ESP32 over serial so the display can show icon activity.
    """
    # Notify ESP32 of tool call (for display icons when using WiFi)
    try:
        from cortex_mcp.server import _notify_esp32
        if hasattr(bridge, '_host'):  # WiFiBridge has _host, serial bridges don't
            _notify_esp32(command)
    except Exception:
        pass

    msg = build_command(command, payload)
    try:
        lines = bridge.send_and_wait(msg, timeout=timeout)
    except (OSError, ConnectionError, TimeoutError) as e:
        # Transport failed — reset bridge for next call and report error
        try:
            from cortex_mcp.server import _reset_bridge
            _reset_bridge()
        except ImportError:
            pass
        return "Transport error (will retry with next transport): {}".format(e)
    except Exception as e:
        # urllib errors (URLError etc.) also indicate WiFi failure
        err_name = type(e).__name__
        if "urlerror" in err_name.lower() or "timeout" in str(e).lower():
            try:
                from cortex_mcp.server import _reset_bridge
                _reset_bridge()
            except ImportError:
                pass
            return "Transport error (will retry with next transport): {}".format(e)
        return "Error: {}".format(e)

    resp = parse_response(lines)
    if resp is None:
        return "No response (timeout). Check Cortex Link and Core are connected."
    if resp["type"] == "ERR":
        return "Error from Core: {}".format(resp["data"])
    if resp["type"] == "ACK":
        return "ACK (id: {})".format(resp["data"])
    if resp["type"] == "RSP":
        data = resp["data"]
        if isinstance(data, dict):
            return json.dumps(data, indent=2)
        return str(data)
    return resp["raw"]
