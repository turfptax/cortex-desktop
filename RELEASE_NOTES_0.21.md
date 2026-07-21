# Cortex Hub v0.21.0 — The Cloud Release

The corpus of record moved off the home Pi and into the cloud
(cortex.turfptax.com, an Azure Container App running the core, the
gateway, an embedding sidecar, and continuous Litestream replication
to Blob storage). This release makes the desktop reflect that reality:
the Hub is now the window onto the cloud corpus, and everything that
only made sense with a physical Pi on the LAN is gone.

## Removed (the Pi era)

- The Pi system tab: hardware status, firmware/SSH update controls.
- The entire LoRA training pipeline: training routers and services,
  pipeline UI, dataset curation, teacher-student synthesis, the dream
  cycle, the cortex_train package, and the training/ scripts.
- Games (Pong + Pong AI training).
- The MCP server's serial/BLE/daemon transport chain (ESP32 dongle
  path) and the shared-serial daemon. One HTTPS bridge remains.
- MCP Pi-hardware tools: wifi_scan, wifi_status, wifi_config, and
  shell_exec (which through the cloud proxy would have been a remote
  shell into the container; removed on security grounds).
- The Settings LAN scanner (sweeping the /24 for a Pi).
- The gateway sync live-forward client (the ATTACH topology made it
  meaningless).

## Changed

- Settings: "Pi Connection" is now "Cortex Cloud" and takes the full
  core URL (the gateway's authenticated /core proxy); Test Connection
  understands URLs.
- The status dot reads "Cortex Cloud" and reflects cloud
  reachability; internal naming moved from pi to core where visible.
- System > Notes: the note capture panel survived the Pi tab's
  removal and lives as its own sub-tab; System now defaults to Data.
- Local LM chat stays (it talks to LM Studio on the workstation, not
  the Pi) but lost its save-to-training-dataset workflow.
- MCP server + CLI: single HTTPS transport with cloud-aware defaults;
  connection_info reports the configured core URL and reachability.
- Tray: cloud-aware health check and "Cloud: Connected/Offline" label.

## Kept, unchanged and now cloud-served

Search, Corpus (all interpretive sub-tabs incl. the Claude-file
import), Chat, Simples, Journal (incl. voice transcription), Data
browser, Activity, Lemon sync, Video/vision, Settings, the /intro
copy-context page, and the full MCP corpus tool surface.
