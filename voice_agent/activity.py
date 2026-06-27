"""Live activity dashboard for the voice agent.

A tiny in-process HTTP server that exposes a real-time X-ray of the agent and is
embedded next to the playground in the Hub Voice tab. It shows:
  - Sub-agent tasks (status -> steps -> result), served from /tasks
  - The conversation + every tool call/result, served from /activity

The pipeline + tools + sub-agents record events via record(); open
http://localhost:<MONITOR_PORT>/ to watch.
"""
from __future__ import annotations

import collections
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from loguru import logger

ACTIVITY: collections.deque = collections.deque(maxlen=160)


def record(kind: str, **kw) -> None:
    """Append an event. kind in: user | assistant | tool_call | tool_result |
    task_start | task_step | task_done."""
    ACTIVITY.append({"ts": time.strftime("%H:%M:%S"), "kind": kind, **kw})


def _tasks_json() -> list:
    """Authoritative sub-agent task list (lazy import avoids an import cycle)."""
    try:
        from . import subagent
        return subagent.list_tasks()
    except Exception:
        return []


_MONITOR_HTML = """<!doctype html><meta charset=utf-8><title>Cortex voice activity</title>
<style>body{font-family:system-ui,sans-serif;background:#0d1117;color:#e6edf3;margin:0;padding:14px}
h2{margin:0 0 8px;font-size:15px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
.row{padding:6px 9px;margin:4px 0;background:#161b22;border-left:3px solid #444;border-radius:5px;font-size:13.5px;line-height:1.4}
.call{border-color:#388bfd}.result{border-color:#3fb950}.ts{color:#8b949e;font-size:11px;margin-right:6px}
.k{font-weight:600;color:#58a6ff}.r{font-weight:600;color:#3fb950}.bad{font-weight:600;color:#f85149}
.user{border-color:#d29922}.assistant{border-color:#a371f7}.u{font-weight:600;color:#d29922}.a{font-weight:600;color:#a371f7}
.empty{color:#8b949e}.sec{margin-bottom:16px}
.task{background:#161b22;border:1px solid #30363d;border-left:3px solid #388bfd;border-radius:6px;padding:8px 10px;margin:6px 0}
.thead{font-size:13px;font-weight:600;color:#e6edf3}.tmodel{color:#8b949e;font-weight:400;font-size:11px;margin-left:4px}
.ttask{font-size:13px;color:#c9d1d9;margin:3px 0 5px}
.step{font-size:12px;color:#8b949e;margin:1px 0 1px 8px}.step:before{content:"› ";color:#388bfd}
.tres{font-size:13px;color:#3fb950;margin-top:5px;border-top:1px solid #21262d;padding-top:5px}
.run{color:#d29922;font-size:11px}.done{color:#3fb950;font-size:11px}.err{color:#f85149;font-size:11px}</style>
<div class=sec><h2>Sub-agent tasks</h2><div id=tasks class=empty>no sub-agents yet</div></div>
<div class=sec><h2>Conversation &amp; tools</h2><div id=feed class=empty>waiting for activity&hellip;</div></div>
<script>
function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}
function taskCard(t){
  const b=t.status==='running'?'<span class=run>● running</span>':t.status==='error'?'<span class=err>● error</span>':'<span class=done>● done</span>';
  const steps=(t.steps||[]).map(s=>`<div class=step>${esc(s)}</div>`).join('');
  const res=t.result?`<div class=tres>${esc(t.result)}</div>`:'';
  return `<div class=task><div class=thead>#${t.id} <span class=tmodel>${esc(t.model)}</span> ${b}</div><div class=ttask>${esc(t.task)}</div>${steps}${res}</div>`;
}
function feedRow(e){
  if(e.kind==='user')return `<div class="row user"><span class=ts>${e.ts}</span><span class=u>you</span> ${esc(e.text)}</div>`;
  if(e.kind==='assistant')return `<div class="row assistant"><span class=ts>${e.ts}</span><span class=a>cortex</span> ${esc(e.text)}</div>`;
  if(e.kind==='tool_call')return `<div class="row call"><span class=ts>${e.ts}</span><span class=k>${esc(e.name)}</span> &rarr; ${esc(e.detail)}</div>`;
  if(e.kind==='tool_result'){const c=e.ok===false?'bad':'r';return `<div class="row result"><span class=ts>${e.ts}</span><span class=${c}>${esc(e.name)}</span> &larr; ${esc(e.detail)}</div>`;}
  return '';
}
async function tick(){
  try{
    const t=await(await fetch('/tasks')).json();const td=document.getElementById('tasks');
    if(!t.length){td.className='empty';td.textContent='no sub-agents yet';}else{td.className='';td.innerHTML=t.map(taskCard).join('');}
    const a=await(await fetch('/activity')).json();const f=document.getElementById('feed');
    const rows=a.slice().reverse().map(feedRow).filter(Boolean);
    if(!rows.length){f.className='empty';f.textContent='waiting for activity…';}else{f.className='';f.innerHTML=rows.join('');}
  }catch(e){}
}
setInterval(tick,1000);tick();
</script>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence default request logging
        pass

    def do_GET(self):
        if self.path.startswith("/activity"):
            body, ctype = json.dumps(list(ACTIVITY)).encode(), "application/json"
        elif self.path.startswith("/tasks"):
            body, ctype = json.dumps(_tasks_json()).encode(), "application/json"
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
