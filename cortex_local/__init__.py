"""cortex_local: the piece of the desktop that stays after the cloud move.

A lightweight local agent that watches Claude Code / Claude Desktop
.jsonl session files (only a process on this machine can see them) and
pushes new sessions to the cloud gateway. Design:
docs/CLOUD_MIGRATION_DESKTOP_PREP.md. Scaffold only; gated on core P3.
"""

__version__ = "0.0.1"
