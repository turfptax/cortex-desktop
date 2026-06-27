"""Live activity monitor for the voice agent.

A tiny in-process HTTP server that exposes a real-time X-ray of the agent: each
user turn (exactly what STT heard), each assistant reply, and every ask_overseer
call + result. Open http://localhost:<MONITOR_PORT>/ alongside the voice UI.

The pipeline records events via record(); the Hub later renders the same feed in
a side panel, so this view is the model for that panel.
"""
from __future__ import annotations

import collections
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from loguru import logger

ACTIVITY: collections.deque = collections.deque(maxlen=120)


def record(kind: str, **kw) -> None:
    """Append an event (kind = user | assistant | tool_call | tool_result)."""
    ACTIVITY.append({"ts": time.strftime("%H:%M:%S"), "kind": kind, **kw})


_MONITOR_HTML = """<!doctype html><meta charset=utf-8><title>Cortex voice activity</title>
<style>body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:14px}
h2{margin:0 0 10px;font-size:16px}.row{padding:7px 10px;margin:5px 0;background:#161b22;
border-left:3px solid #444;border-radius:5px;font-size:14px;line-height:1.4}
.call{border-color:#388bfd}.result{border-color:#3fb950}.ts{color:#8b949e;font-size:11px;margin-right:6px}
.k{font-weight:600;color:#58a6ff}.r{font-weight:600;color:#3fb950}.empty{color:#8b949e}
.user{border-color:#d29922}.assistant{border-color:#a371f7}.u{font-weight:600;color:#d29922}.a{font-weight:600;color:#a371f7}</style>
<h2>Cortex voice &mdash; agent activity</h2><div id=feed class=empty>waiting for activity&hellip;</div>
<script>
async function tick(){try{const a=await(await fetch('/activity')).json();const f=document.getElementById('feed');
if(!a.length){f.className='empty';f.textContent='waiting for activity…';return;}f.className='';
f.innerHTML=a.slice().reverse().map(e=>{
if(e.kind==='user')return `<div class="row user"><span class=ts>${e.ts}</span><span class=u>you</span> ${e.text||''}</div>`;
if(e.kind==='assistant')return `<div class="row assistant"><span class=ts>${e.ts}</span><span class=a>cortex</span> ${e.text||''}</div>`;
if(e.kind==='tool_call')return `<div class="row call"><span class=ts>${e.ts}</span><span class=k>ask_overseer</span> &rarr; ${e.question||''}</div>`;
return `<div class="row result"><span class=ts>${e.ts}</span><span class=r>${e.answered_by||'result'}</span> &larr; ${e.answer||''}</div>`;}).join('');
}catch(e){}}
setInterval(tick,1000);tick();
</script>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence default request logging
        pass

    def do_GET(self):
        if self.path.startswith("/activity"):
            body, ctype = json.dumps(list(ACTIVITY)).encode(), "application/json"
        else:
            body, ctype = _MONITOR_HTML.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_monitor(port: int) -> None:
    """Start the monitor HTTP server on a daemon thread."""
    srv = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    logger.info(f"activity monitor -> http://localhost:{port}/")
