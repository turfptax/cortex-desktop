"""Serial bridge to Cortex Link (ESP32-S3 USB-CDC).

Manages the USB serial connection with background reading and
thread-safe send/receive. Auto-detects ESP32-S3 devices.
"""

import json
import os
import time
import threading
from collections import deque
from pathlib import Path

import serial
import serial.tools.list_ports

# Discovery config saved when Pi announces itself over BLE
DISCOVERY_FILE = Path.home() / ".cortex-wifi.json"

# ESP32-S3 USB-CDC vendor ID
ESP32_S3_VID = 0x303A


def find_esp32_port():
    """Auto-detect an ESP32-S3 USB-CDC serial port.

    Returns the port device string (e.g. 'COM5', '/dev/ttyACM0') or None.
    """
    for p in serial.tools.list_ports.comports():
        if p.vid == ESP32_S3_VID:
            return p.device
        if p.description and "ESP32" in p.description.upper():
            return p.device
    return None


def list_ports():
    """List all serial ports with descriptions."""
    result = []
    for p in serial.tools.list_ports.comports():
        desc = p.description or "unknown"
        vid = "VID={:04X}".format(p.vid) if p.vid else ""
        result.append("{} - {} {}".format(p.device, desc, vid).strip())
    return result


class SerialBridge:
    """Manages the serial connection to the ESP32 and background reading."""

    def __init__(self, port=None, baud=None, timeout=None):
        self._port = (
            port
            or os.environ.get("CORTEX_PORT")
            or os.environ.get("KEYMASTER_PORT")
            or ""
        )
        self._baud = int(
            baud
            or os.environ.get("CORTEX_BAUD")
            or os.environ.get("KEYMASTER_BAUD")
            or "115200"
        )
        self._timeout = float(
            timeout
            or os.environ.get("CORTEX_TIMEOUT")
            or os.environ.get("KEYMASTER_TIMEOUT")
            or "5"
        )

        self._ser = None
        self._lock = threading.Lock()
        self._rx_queue = deque(maxlen=500)
        self._reader_thread = None

    @property
    def is_connected(self):
        return self._ser is not None and self._ser.is_open

    @property
    def port_name(self):
        if self._ser:
            return self._ser.port
        return None

    @property
    def baud_rate(self):
        return self._baud

    @property
    def buffered_count(self):
        return len(self._rx_queue)

    @property
    def default_timeout(self):
        return self._timeout

    def connect(self, port=None, baud=None):
        """Open the serial port. Auto-detects ESP32 if port is None."""
        if self.is_connected:
            return

        port = port or self._port or find_esp32_port()
        baud = baud or self._baud
        if not port:
            raise ConnectionError(
                "Cortex Link (ESP32) not found. Set CORTEX_PORT env var "
                "(e.g. COM5 or /dev/ttyACM0) or plug in the device."
            )

        self._ser = serial.Serial(port, baud, timeout=0.1)
        time.sleep(0.5)
        self._ser.reset_input_buffer()

        if self._reader_thread is None or not self._reader_thread.is_alive():
            self._reader_thread = threading.Thread(
                target=self._reader_loop, daemon=True
            )
            self._reader_thread.start()

    def disconnect(self):
        if self._ser and self._ser.is_open:
            self._ser.close()
        self._ser = None

    def send(self, message):
        """Send a newline-delimited message."""
        self._ensure_connected()
        if not message.endswith("\n"):
            message += "\n"
        encoded = message.encode("utf-8")
        try:
            with self._lock:
                self._ser.write(encoded)
        except (serial.SerialException, PermissionError, OSError):
            self._reconnect()
            with self._lock:
                self._ser.write(encoded)

    def send_and_wait(self, message, timeout=None, settle=0.4):
        """Send a message and collect response lines.

        After the first response line arrives, waits an additional `settle`
        seconds for more lines before returning.
        """
        timeout = timeout or self._timeout
        t0 = time.time()

        self._rx_queue.clear()
        self.send(message)

        lines = []
        deadline = t0 + timeout
        first_at = None

        while time.time() < deadline:
            while self._rx_queue:
                ts, text = self._rx_queue.popleft()
                if ts >= t0:
                    lines.append(text)
                    if first_at is None:
                        first_at = time.time()

            if first_at is not None and (time.time() - first_at) >= settle:
                while self._rx_queue:
                    ts, text = self._rx_queue.popleft()
                    if ts >= t0:
                        lines.append(text)
                break

            time.sleep(0.05)

        return lines

    def read_pending(self):
        """Return all buffered messages without sending anything."""
        lines = []
        while self._rx_queue:
            _ts, text = self._rx_queue.popleft()
            lines.append(text)
        return lines

    def _reconnect(self):
        port = self.port_name
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._ser = None
        time.sleep(1.0)
        self.connect(port=port)

    def _ensure_connected(self):
        if not self.is_connected:
            self.connect()

    @staticmethod
    def _handle_discovery(line):
        """Intercept DISCOVER: messages from Pi and save WiFi config (ip + port)."""
        if not line.startswith("DISCOVER:"):
            return False
        try:
            payload = json.loads(line[9:])
            ip = payload.get("ip")
            if not ip:
                return True

            # Save discovery config (ip + port for WiFi bridge)
            DISCOVERY_FILE.write_text(
                json.dumps(payload, indent=2) + "\n", encoding="utf-8"
            )

        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return True

    def _reader_loop(self):
        """Background thread: read serial lines into the queue."""
        buf = b""
        while True:
            try:
                if self._ser and self._ser.is_open:
                    chunk = self._ser.read(512)
                    if chunk:
                        buf += chunk
                        while b"\n" in buf:
                            idx = buf.index(b"\n")
                            line = buf[:idx].decode("utf-8", errors="replace").strip()
                            buf = buf[idx + 1:]
                            if line:
                                # Intercept discovery messages from Pi
                                self._handle_discovery(line)
                                self._rx_queue.append((time.time(), line))
                else:
                    time.sleep(0.2)
            except (serial.SerialException, PermissionError, OSError):
                buf = b""
                try:
                    self._reconnect()
                except Exception:
                    time.sleep(2.0)
            except Exception:
                time.sleep(0.5)
