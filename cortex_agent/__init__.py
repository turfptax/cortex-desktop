"""Cortex Ingest Agent: the reinvented cortex-desktop.

A tray-scheduled local ingestor for CortexGraph. It watches what only
this machine can see (Claude Code sessions first), crunches locally,
and writes through the graph's MCP connector surface over OAuth. It
renders no corpus content and holds no database credentials.

Spec of record: docs/CORTEX_GRAPH_INGESTOR_PLAN.md.
"""

__version__ = "0.1.1"
