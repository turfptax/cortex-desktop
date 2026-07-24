"""cortex_local: the piece of the desktop that stays after the cloud move.

A lightweight local agent that watches Claude Code .jsonl session
files (only a process on this machine can see them) and pushes new or
grown sessions to the cloud corpus. Spec of record:
docs/CORTEX_AGENT_PLAN.md (CP1 shipped as v0.22.0).
"""

__version__ = "0.1.0"
