# Cortex Desktop

A Windows desktop application for **Cortex Hub** — a web-based control panel for the [Cortex](https://github.com/turfptax) AI companion system.

Cortex Desktop runs as a system tray app, serving the Hub UI in your default browser. One click to launch, one click to open.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## What is Cortex?

Cortex is a personal AI companion that runs on a Raspberry Pi (Orange Pi Zero 2W). It features:

- **Tamagotchi-style pet** with vitals, mood, and evolution
- **On-device LLM** (Qwen 0.8B) for local chat
- **Training pipeline** for fine-tuning personality
- **Hardware**: LCD display, speaker, gamepad input
- **MCP integration** with Claude Code / Claude Desktop

Cortex Desktop gives you a browser-based dashboard to interact with your Pi from your PC.

## Features

- **System tray icon** with Pi connection status (green = connected, red = offline)
- **Auto-launches browser** to the Hub UI on startup
- **Chat** with your Cortex pet via local LLM
- **Training** management for fine-tuning
- **Data browser** for the Pi's SQLite database (full CRUD)
- **Games** and interactive features
- **Pi status** monitoring and control

## Quick Start (Development)

### Prerequisites

- Python 3.10+
- Node.js 18+ (for building the frontend)

### 1. Clone and install

```bash
git clone https://github.com/turfptax/cortex-desktop.git
cd cortex-desktop

# Install Python dependencies
pip install -e .

# Install frontend dependencies
cd hub/frontend
npm install
cd ../..
```

### 2. Build the frontend

```bash
cd hub/frontend
npm run build
cd ../..
```

### 3. Run

```bash
python -m cortex_desktop
```

This will:
- Start the backend server on `http://localhost:8003`
- Serve the frontend UI
- Show a system tray icon (purple C with status dot)
- Open your browser to the Hub

### 4. Configure

On first run, a config file is created at:
- **Windows**: `%APPDATA%\Cortex\config.json`
- **Linux/Mac**: `~/.config/cortex/config.json`

Edit it to set your Pi's IP address:

```json
{
  "pi_host": "10.0.0.25",
  "pi_port": 8420,
  "pi_username": "cortex",
  "pi_password": "cortex",
  "hub_port": 8003,
  "auto_open_browser": true
}
```

## Building a Standalone .exe

Build a self-contained Windows executable (no Python/Node required to run):

```bash
# Install build dependencies
pip install pyinstaller

# Full build (frontend + PyInstaller)
python build.py

# Or skip frontend rebuild
python build.py --skip-frontend
```

Output: `dist/CortexHub/CortexHub.exe`

## Project Structure

```
cortex-desktop/
  cortex_desktop/           # Python package
    app.py                  # Main entry: uvicorn + system tray
    tray.py                 # System tray icon and menu
    config.py               # Configuration management
  hub/
    backend/                # FastAPI backend (API server)
      main.py               # FastAPI app
      config.py             # Backend settings
      routers/              # API route handlers
      services/             # Pi client, LM Studio client
    frontend/               # React frontend (Vite + Tailwind)
      src/                  # React components
      package.json          # Node dependencies
  assets/                   # Tray icon files
  build.py                  # Build orchestrator
  cortex_desktop.spec       # PyInstaller spec
  pyproject.toml            # Python package config
```

## How It Works

1. **`app.py`** loads config from `%APPDATA%/Cortex/config.json`
2. Sets environment variables so the FastAPI backend picks up Pi connection settings
3. Starts **uvicorn** (FastAPI) in a background thread, serving both the API and the pre-built React SPA
4. Runs **pystray** on the main thread for the Windows system tray icon
5. The tray icon polls the Pi every 30s and updates the status dot (green/red)

## Cortex Ecosystem

Cortex is a multi-device AI companion system. Here are all the repos:

| Repo | Description |
|------|-------------|
| **[cortex-desktop](https://github.com/turfptax/cortex-desktop)** | This repo — Windows desktop app (system tray + browser UI) |
| **[cortex-core](https://github.com/turfptax/cortex-core)** | Brain firmware for the Pi — pet engine, LLM inference, display, audio, BLE |
| **[cortex-link](https://github.com/turfptax/cortex-link)** | ESP32-S3 BLE bridge — USB serial ↔ BLE relay between PC and Pi |
| **[cortex-mcp](https://github.com/turfptax/cortex-mcp)** | MCP server for Claude Code / Claude Desktop integration |
| **[cortex-plugin](https://github.com/turfptax/cortex-plugin)** | Claude Code plugin with skills and commands |
| **[cortex-voice-training](https://github.com/turfptax/cortex-voice-training)** | Whisper fine-tuning pipeline for improved on-device STT |
| **[orangepi-whisplay](https://github.com/turfptax/orangepi-whisplay)** | WhisPlay HAT setup for Orange Pi Zero 2W — device tree overlays, WM8960 audio, GPIO |

### Architecture

```
┌──────────────┐     WiFi/HTTP      ┌──────────────────────┐
│   Cortex     │◄──────────────────►│   Orange Pi Zero 2W  │
│   Desktop    │     :8420          │   (cortex-core)      │
│   (this app) │                    │   - Pet engine       │
└──────┬───────┘                    │   - Qwen 0.8B LLM   │
       │                            │   - LCD + Speaker    │
       │ localhost:8003             │   - Gamepad input    │
       ▼                            └──────────┬───────────┘
  ┌──────────┐                                 │ BLE
  │ Browser  │                                 ▼
  │ (Hub UI) │                     ┌──────────────────────┐
  └──────────┘                     │   ESP32-S3           │
                                   │   (cortex-link)      │
  ┌──────────────┐    USB serial   │   - BLE ↔ USB bridge │
  │ Claude Code  │◄───────────────►│   - LCD status       │
  │ (cortex-mcp) │    stdio        └──────────────────────┘
  └──────────────┘
```

## Hardware

To build your own Cortex companion, you need:

| Component | Purpose | Setup Guide |
|-----------|---------|-------------|
| **Orange Pi Zero 2W** (2GB) | Main brain — runs cortex-core | [orangepi-whisplay](https://github.com/turfptax/orangepi-whisplay) |
| **WhisPlay HAT** (WM8960) | Audio output — speaker for pet sounds | [orangepi-whisplay](https://github.com/turfptax/orangepi-whisplay) |
| **ST7789 SPI LCD** (240x280) | Display — pet face, status, menus | Included in cortex-core |
| **8BitDo Micro** gamepad | Input — feed, clean, navigate | Bluetooth pairing |
| **Waveshare ESP32-S3-LCD-1.47** | BLE bridge — connects PC to Pi wirelessly | [cortex-link](https://github.com/turfptax/cortex-link) |

The ESP32 bridge is optional — Cortex Desktop communicates with the Pi directly over WiFi.

## License

MIT
