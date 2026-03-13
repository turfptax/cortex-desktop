# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for Cortex Desktop."""

import os
from pathlib import Path

block_cipher = None

ROOT = Path(SPECPATH)
BACKEND_DIR = ROOT / "hub" / "backend"
FRONTEND_DIST = ROOT / "frontend_dist"
ASSETS_DIR = ROOT / "assets"

# Collect data files
datas = []

# Backend Python files
if BACKEND_DIR.is_dir():
    for py_file in BACKEND_DIR.rglob("*.py"):
        rel = py_file.relative_to(BACKEND_DIR)
        dest = Path("backend") / rel.parent
        datas.append((str(py_file), str(dest)))

# Frontend dist (pre-built React SPA)
if FRONTEND_DIST.is_dir():
    datas.append((str(FRONTEND_DIST), "frontend_dist"))

# Assets
if ASSETS_DIR.is_dir():
    datas.append((str(ASSETS_DIR), "assets"))


a = Analysis(
    [str(ROOT / "cortex_desktop" / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # uvicorn internals
        "uvicorn.lifespan.on",
        "uvicorn.lifespan.off",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.wsproto_impl",
        "uvicorn.logging",
        # FastAPI / Starlette
        "fastapi",
        "fastapi.middleware",
        "fastapi.middleware.cors",
        "fastapi.staticfiles",
        "fastapi.responses",
        "fastapi.routing",
        "starlette.responses",
        "starlette.staticfiles",
        "starlette.routing",
        "starlette.middleware",
        "starlette.middleware.cors",
        # httpx / httpcore
        "httpx",
        "httpcore",
        "httpcore._async",
        "httpcore._sync",
        "anyio",
        "anyio._backends._asyncio",
        # pydantic
        "pydantic",
        "pydantic_settings",
        # pystray
        "pystray",
        "pystray._win32",
        # PIL
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        # MCP server
        "mcp",
        "mcp.server",
        "mcp.server.fastmcp",
        "cortex_mcp",
        "cortex_mcp.server",
        "cortex_mcp.bridge",
        "cortex_mcp.protocol",
        "cortex_mcp.wifi_bridge",
        "cortex_mcp.daemon_client",
        "serial",
        "serial.tools",
        "serial.tools.list_ports",
        "click",
        # Other
        "h11",
        "email.mime.text",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "unittest",
        "xmlrpc",
        "pdb",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CortexHub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,  # Needed for --mcp stdio mode; tray mode hides the window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ASSETS_DIR / "icon.ico") if (ASSETS_DIR / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CortexHub",
)
