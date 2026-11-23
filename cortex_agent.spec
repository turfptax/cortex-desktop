# PyInstaller spec for the Cortex Ingest Agent (the slim exe).
# Deliberately excludes the legacy Hub stack and the whole graph engine:
# the agent is an MCP client; cortexgraph never enters this bundle.

a = Analysis(
    ["cortex_agent\\app.py"],
    pathex=["."],
    binaries=[],
    datas=[("assets/CortexIcon.png", "assets")],
    hiddenimports=["pystray._win32"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # NOT starlette/uvicorn: the mcp package's import chain needs them
        # even when only the client half runs (learned on the first build).
        "tkinter", "fastapi", "pydantic_settings",
        "cortexgraph", "gremlin_python", "sqlalchemy", "psycopg",
        # Build and test tools the import graph reaches only through
        # optional paths (anyio's pytest runner, the httpx command line
        # client). They shipped in the first builds; nothing in the agent
        # runs them, and every bundled package is one more to keep patched.
        "pytest", "_pytest", "pygments",
        "setuptools", "_distutils_hack", "pkg_resources", "distutils",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="CortexIngest",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CortexIngest",
)
