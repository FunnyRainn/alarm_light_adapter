from __future__ import annotations

import threading
import time
from dataclasses import asdict
from datetime import datetime
import os
from typing import Any

from .config import AdapterConfig
from .modbus import write_single_coil_frame


COILS = {
    "red": 0,
    "yellow": 1,
    "green": 2,
    "buzzer": 3,
}


class SerialLightController:
    def __init__(self, config: AdapterConfig) -> None:
        self.config = config
        self._lock = threading.Lock()
        self.last_error: str = ""
        self.last_command_at: str | None = None

    def all_on(self) -> None:
        self.set_outputs(True)

    def all_off(self) -> None:
        self.set_outputs(False)

    def set_outputs(self, enabled: bool) -> None:
        frames = [write_single_coil_frame(1, coil, enabled) for coil in COILS.values()]
        self._write_frames(frames)

    def health_check(self) -> dict[str, Any]:
        ok = False
        error = ""
        try:
            with self._open_serial() as port:
                ok = bool(port.is_open)
        except Exception as exc:
            error = str(exc)
        return {
            "serial_port": self.config.serial_port,
            "baudrate": self.config.baudrate,
            "serial_ok": ok,
            "last_error": self.last_error or error,
            "last_command_at": self.last_command_at,
        }

    def _write_frames(self, frames: list[bytes]) -> None:
        with self._lock:
            try:
                with self._open_serial() as port:
                    for frame in frames:
                        port.write(frame)
                        port.flush()
                        time.sleep(0.05)
                self.last_error = ""
                self.last_command_at = datetime.now().isoformat(timespec="milliseconds")
            except Exception as exc:
                self.last_error = str(exc)
                raise

    def _open_serial(self):
        try:
            import serial  # type: ignore
        except ImportError:
            if os.name == "nt":
                return _WinSerialPort(
                    port=self.config.serial_port,
                    baudrate=self.config.baudrate,
                    read_timeout_seconds=self.config.read_timeout_seconds,
                    write_timeout_seconds=self.config.write_timeout_seconds,
                )
            raise RuntimeError("pyserial is not installed. Install requirements.txt first.")

        return serial.Serial(
            port=self.config.serial_port,
            baudrate=self.config.baudrate,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self.config.read_timeout_seconds,
            write_timeout=self.config.write_timeout_seconds,
        )

    def config_snapshot(self) -> dict[str, Any]:
        return asdict(self.config)


class _WinSerialPort:
    """Minimal Windows serial writer used when pyserial is not installed."""

    def __init__(
        self,
        *,
        port: str,
        baudrate: int,
        read_timeout_seconds: float,
        write_timeout_seconds: float,
    ) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._port = port
        self._baudrate = baudrate
        self._read_timeout_ms = int(read_timeout_seconds * 1000)
        self._write_timeout_ms = int(write_timeout_seconds * 1000)
        self._handle = None
        self.is_open = False

    def __enter__(self) -> "_WinSerialPort":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        ctypes = self._ctypes
        wintypes = self._wintypes
        kernel32 = self._kernel32

        class DCB(ctypes.Structure):
            _fields_ = [
                ("DCBlength", wintypes.DWORD),
                ("BaudRate", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("wReserved", wintypes.WORD),
                ("XonLim", wintypes.WORD),
                ("XoffLim", wintypes.WORD),
                ("ByteSize", wintypes.BYTE),
                ("Parity", wintypes.BYTE),
                ("StopBits", wintypes.BYTE),
                ("XonChar", ctypes.c_char),
                ("XoffChar", ctypes.c_char),
                ("ErrorChar", ctypes.c_char),
                ("EofChar", ctypes.c_char),
                ("EvtChar", ctypes.c_char),
                ("wReserved1", wintypes.WORD),
            ]

        class COMMTIMEOUTS(ctypes.Structure):
            _fields_ = [
                ("ReadIntervalTimeout", wintypes.DWORD),
                ("ReadTotalTimeoutMultiplier", wintypes.DWORD),
                ("ReadTotalTimeoutConstant", wintypes.DWORD),
                ("WriteTotalTimeoutMultiplier", wintypes.DWORD),
                ("WriteTotalTimeoutConstant", wintypes.DWORD),
            ]

        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.GetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(DCB)]
        kernel32.GetCommState.restype = wintypes.BOOL
        kernel32.BuildCommDCBW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(DCB)]
        kernel32.BuildCommDCBW.restype = wintypes.BOOL
        kernel32.SetCommState.argtypes = [wintypes.HANDLE, ctypes.POINTER(DCB)]
        kernel32.SetCommState.restype = wintypes.BOOL
        kernel32.SetCommTimeouts.argtypes = [wintypes.HANDLE, ctypes.POINTER(COMMTIMEOUTS)]
        kernel32.SetCommTimeouts.restype = wintypes.BOOL

        path = self._port if self._port.startswith("\\\\.\\") else f"\\\\.\\{self._port}"
        handle = kernel32.CreateFileW(
            path,
            0x80000000 | 0x40000000,
            0,
            None,
            3,
            0,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            self._raise_last_error(f"open {self._port}")

        self._handle = handle
        dcb = DCB()
        dcb.DCBlength = ctypes.sizeof(DCB)
        if not kernel32.GetCommState(handle, ctypes.byref(dcb)):
            self.close()
            self._raise_last_error("GetCommState")
        mode = f"baud={self._baudrate} parity=N data=8 stop=1"
        if not kernel32.BuildCommDCBW(mode, ctypes.byref(dcb)):
            self.close()
            self._raise_last_error("BuildCommDCB")
        if not kernel32.SetCommState(handle, ctypes.byref(dcb)):
            self.close()
            self._raise_last_error("SetCommState")
        timeouts = COMMTIMEOUTS(
            ReadIntervalTimeout=self._read_timeout_ms,
            ReadTotalTimeoutMultiplier=0,
            ReadTotalTimeoutConstant=self._read_timeout_ms,
            WriteTotalTimeoutMultiplier=0,
            WriteTotalTimeoutConstant=self._write_timeout_ms,
        )
        if not kernel32.SetCommTimeouts(handle, ctypes.byref(timeouts)):
            self.close()
            self._raise_last_error("SetCommTimeouts")
        self.is_open = True

    def write(self, data: bytes) -> int:
        ctypes = self._ctypes
        wintypes = self._wintypes
        kernel32 = self._kernel32
        if self._handle is None:
            raise RuntimeError("serial port is not open")
        kernel32.WriteFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        kernel32.WriteFile.restype = wintypes.BOOL
        written = wintypes.DWORD(0)
        buffer = ctypes.create_string_buffer(data)
        if not kernel32.WriteFile(self._handle, buffer, len(data), ctypes.byref(written), None):
            self._raise_last_error("WriteFile")
        return int(written.value)

    def flush(self) -> None:
        if self._handle is None:
            return
        self._kernel32.FlushFileBuffers(self._handle)

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None
        self.is_open = False

    def _raise_last_error(self, action: str) -> None:
        ctypes = self._ctypes
        error = ctypes.get_last_error()
        raise OSError(error, f"Windows serial {action} failed")


class AlarmPatternRunner:
    def __init__(self, controller: SerialLightController, config: AdapterConfig) -> None:
        self.controller = controller
        self.config = config
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._deadline = 0.0
        self._thread: threading.Thread | None = None
        self._active = False
        self.last_triggered_at: str | None = None
        self.last_error: str = ""

    def trigger(self) -> None:
        import time

        with self._lock:
            self._deadline = time.monotonic() + self.config.alarm_duration_seconds
            self.last_triggered_at = datetime.now().isoformat(timespec="milliseconds")
            self._stop_event.clear()
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="alarm-light-pattern", daemon=True)
                self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self.controller.all_off()
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self._active = False

    def is_active(self) -> bool:
        return self._active

    def status(self) -> dict[str, Any]:
        return {
            "active": self._active,
            "last_triggered_at": self.last_triggered_at,
            "last_error": self.last_error,
        }

    def _run(self) -> None:
        import time

        self._active = True
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    deadline = self._deadline
                if time.monotonic() >= deadline:
                    break
                self.controller.all_on()
                if self._stop_event.wait(self.config.flash_on_ms / 1000.0):
                    break
                self.controller.all_off()
                if self._stop_event.wait(self.config.flash_off_ms / 1000.0):
                    break
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            try:
                self.controller.all_off()
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                self._active = False
