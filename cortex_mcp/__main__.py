"""Allow running cortex_mcp as a module: python -m cortex_mcp

Dispatches to the CLI so all commands work without PATH setup on Windows:
    python -m cortex_mcp setup --target claude-desktop
    python -m cortex_mcp ping
    python -m cortex_mcp daemon status
"""

from cortex_mcp.cli import main

if __name__ == "__main__":
    main()
