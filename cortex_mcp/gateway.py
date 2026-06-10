"""Azure Gateway client for the daemon's bridge-side sync forwarding.

Sync contract v2 (docs/SYNC_CONTRACT_DRAFT.md, RATIFIED 2026-06-10):
over the BLE bridge the desktop stays STATELESS - it live-forwards
sync messages to the Gateway's /v1/sync/* endpoints and relays the
response. No Gateway reach -> the daemon answers ERR:sync_*:offline
and the phone keeps its rows queued locally.

Configuration (resolution: env > config.json > unset):
    CORTEX_GATEWAY_URL   / config.json "gateway_url"
    CORTEX_GATEWAY_TOKEN / config.json "gateway_token" (app scope)

Token-agnostic by design: until provisioning lands (asked of the
mobile stream in docs/team-mail/TO_MOBILE.md 2026-06-10), every sync
forward cleanly reports offline.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request

from cortex_mcp.wifi_bridge import _load_user_config

log = logging.getLogger("cortex.gateway")

_TIMEOUT = 20  # Gateway is a cloud round-trip; BLE caller is patient


def get_gateway_config():
    """Return (base_url, token); either may be "" when unprovisioned."""
    user = _load_user_config()
    url = (os.environ.get("CORTEX_GATEWAY_URL")
           or user.get("gateway_url") or "").rstrip("/")
    token = (os.environ.get("CORTEX_GATEWAY_TOKEN")
             or user.get("gateway_token") or "")
    return url, token


def forward_sync(kind, payload):
    """Forward one sync message to the Gateway per the contract's
    transport mapping. kind is 'sync_push' | 'sync_pull' |
    'sync_status'; payload is the parsed CMD JSON body.

    Returns the Gateway's response dict, or None when the Gateway is
    unreachable/unprovisioned (caller answers ERR:<kind>:offline).
    """
    url, token = get_gateway_config()
    if not url or not token:
        return None
    headers = {"Authorization": "Bearer " + token}
    try:
        if kind == "sync_status":
            qs = urllib.parse.urlencode(
                {"device": payload.get("device", "")})
            req = urllib.request.Request(
                "{}/v1/sync/status?{}".format(url, qs),
                headers=headers, method="GET")
        else:
            endpoint = "push" if kind == "sync_push" else "pull"
            req = urllib.request.Request(
                "{}/v1/sync/{}".format(url, endpoint),
                data=json.dumps(payload).encode("utf-8"),
                headers={**headers, "Content-Type": "application/json"},
                method="POST")
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8",
                                                 errors="replace"))
    except urllib.error.HTTPError as e:
        # The Gateway answered; relay its error rather than 'offline'.
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            body = ""
        log.warning("gateway %s HTTP %s: %s", kind, e.code, body)
        return {"ok": False,
                "error": "gateway HTTP {}".format(e.code)}
    except Exception as e:
        log.warning("gateway %s unreachable: %s", kind, e)
        return None
