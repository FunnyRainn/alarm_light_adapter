from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
import os
from typing import Any, Callable

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
        self.set_channels(red=True, yellow=True, green=True, buzzer=True)

    def all_off(self) -> None:
        self.set_channels()

    def set_outputs(self, enabled: bool) -> None:
        """保留旧的全开/全关入口，内部改用明确通道组合。"""

        self.set_channels(
            red=enabled,
            yellow=enabled,
            green=enabled,
            buzzer=enabled,
        )

    def set_channels(
        self,
        *,
        red: bool = False,
        yellow: bool = False,
        green: bool = False,
        buzzer: bool = False,
    ) -> None:
        """一次串口会话写完四个线圈，未选通道也显式关闭，避免残留状态。"""

        states = {
            "red": bool(red),
            "yellow": bool(yellow),
            "green": bool(green),
            "buzzer": bool(buzzer),
        }
        frames = [write_single_coil_frame(1, coil, states[name]) for name, coil in COILS.items()]
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
        return self.config.snapshot()


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


@dataclass
class _AlarmLease:
    """单个事故在适配器内的租约；新事故循环蜂鸣，旧接口只单次蜂鸣。"""

    severity: str
    expires_at: float
    periodic_buzzer: bool


class AlarmPatternRunner:
    """按 incident_id 维护租约并输出当前未过期事故中的最高等级模板。"""

    _SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}

    def __init__(
        self,
        controller: SerialLightController,
        config: AdapterConfig,
        *,
        clock: Callable[[], float] | None = None,
        auto_start: bool = True,
    ) -> None:
        self.controller = controller
        self.config = config
        self._clock = clock or time.monotonic
        self._auto_start = bool(auto_start)
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._leases: dict[str, _AlarmLease] = {}
        # 旧 `/alarm` 与 `/test` 仍按单次蜂鸣截止时间执行，绝不进入事故循环。
        self._one_shot_buzz_deadlines: dict[str, float] = {}
        # 周期相位只属于当前最高等级，而不属于某个 incident，避免同级并发反复重启。
        self._buzzer_cycle_severity: str | None = None
        self._buzzer_cycle_started_at: float | None = None
        self._last_outputs: tuple[bool, bool, bool, bool] | None = None
        self._effective_severity: str | None = None
        self.last_triggered_at: str | None = None
        self.last_action: str = ""
        self.last_error: str = ""

    def apply(self, *, incident_id: str, severity: str, action: str) -> None:
        """执行新事故 raise/refresh/resolve；续租只延长到期时间，不直接改周期相位。"""

        normalized_id = str(incident_id or "").strip()
        normalized_severity = str(severity or "").strip().lower()
        normalized_action = str(action or "").strip().lower()
        if not normalized_id:
            raise ValueError("incident_id is required")
        if normalized_severity not in self._SEVERITY_ORDER:
            raise ValueError(f"unsupported alarm severity: {severity}")
        if normalized_action not in {"raise", "refresh", "resolve"}:
            raise ValueError(f"unsupported alarm action: {action}")
        now_value = self._clock()
        with self._lock:
            self._expire_locked(now_value)
            if normalized_action == "resolve":
                self._leases.pop(normalized_id, None)
                self._one_shot_buzz_deadlines.pop(normalized_id, None)
            else:
                self._leases[normalized_id] = _AlarmLease(
                    severity=normalized_severity,
                    expires_at=now_value + self.config.lease_seconds,
                    periodic_buzzer=True,
                )
            self.last_triggered_at = datetime.now().isoformat(timespec="milliseconds")
            self.last_action = normalized_action
            # 对一个本就不存在的事故执行幂等 resolve 时无需凭空启动常驻线程。
            if self._auto_start and (
                self._leases
                or (self._thread is not None and self._thread.is_alive())
            ):
                self._ensure_thread_locked()
        self._wake_event.set()

    def trigger(self, *, incident_id: str = "legacy") -> None:
        """旧 `/alarm` 的中度单次入口；同一租约内重复调用只续租。"""

        normalized_id = str(incident_id or "").strip()
        if not normalized_id:
            raise ValueError("incident_id is required")
        now_value = self._clock()
        with self._lock:
            self._expire_locked(now_value)
            existing = self._leases.get(normalized_id)
            self._leases[normalized_id] = _AlarmLease(
                severity="medium",
                expires_at=now_value + self.config.lease_seconds,
                periodic_buzzer=False,
            )
            # 同一个旧接口租约内重复调用只续租；租约真正结束后再次调用才重新单次蜂鸣。
            if existing is None:
                buzzer_seconds = self.config.profile("medium").buzzer_seconds
                if buzzer_seconds > 0:
                    self._one_shot_buzz_deadlines[normalized_id] = now_value + buzzer_seconds
            self.last_triggered_at = datetime.now().isoformat(timespec="milliseconds")
            self.last_action = "raise"
            if self._auto_start:
                self._ensure_thread_locked()
        self._wake_event.set()

    def stop(self) -> None:
        """清空全部租约、有界回收线程并保证所有线圈关闭。"""

        with self._lock:
            self._leases.clear()
            self._one_shot_buzz_deadlines.clear()
            self._reset_buzzer_cycle_locked()
            thread = self._thread
        self._stop_event.set()
        self._wake_event.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        try:
            self.controller.all_off()
            with self._lock:
                self._last_outputs = (False, False, False, False)
                self._effective_severity = None
                self._reset_buzzer_cycle_locked()
            self.last_error = ""
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            with self._lock:
                if thread is None or not thread.is_alive():
                    self._thread = None

    def is_active(self) -> bool:
        """只根据未过期租约判断 active，不把线程存活误当业务报警。"""

        with self._lock:
            self._expire_locked(self._clock())
            return bool(self._leases)

    def status(self) -> dict[str, Any]:
        """返回健康接口所需的小型租约摘要，不暴露内部线程对象。"""

        with self._lock:
            self._expire_locked(self._clock())
            effective = self._select_effective_locked()
            lease_count = len(self._leases)
        return {
            "active": bool(lease_count),
            "lease_count": lease_count,
            "effective_severity": effective,
            "last_triggered_at": self.last_triggered_at,
            "last_action": self.last_action,
            "last_error": self.last_error,
        }

    def tick(self) -> tuple[bool, bool, bool, bool]:
        """计算并写出当前组合；公开该小步仅用于无串口、无 sleep 的确定性测试。"""

        now_value = self._clock()
        with self._lock:
            self._expire_locked(now_value)
            severity = self._select_effective_locked()
            self._effective_severity = severity
            lights_on = False
            lights: tuple[str, ...] = ()
            if severity is not None:
                profile = self.config.profile(severity)
                lights = profile.lights
                cycle_seconds = (self.config.flash_on_ms + self.config.flash_off_ms) / 1000.0
                phase_seconds = now_value % cycle_seconds
                lights_on = phase_seconds < self.config.flash_on_ms / 1000.0
            periodic_buzzer_on = self._periodic_buzzer_on_locked(now_value, severity)
            one_shot_buzzer_on = any(
                deadline > now_value
                for deadline in self._one_shot_buzz_deadlines.values()
            )
            buzzer_on = periodic_buzzer_on or one_shot_buzzer_on
            desired = (
                lights_on and "red" in lights,
                lights_on and "yellow" in lights,
                lights_on and "green" in lights,
                buzzer_on,
            )
            unchanged = desired == self._last_outputs
        if unchanged:
            return desired
        try:
            self.controller.set_channels(
                red=desired[0],
                yellow=desired[1],
                green=desired[2],
                buzzer=desired[3],
            )
            with self._lock:
                self._last_outputs = desired
            self.last_error = ""
        except Exception as exc:
            # 写失败时不更新 last_outputs，下一 tick 会再次尝试同一安全组合。
            self.last_error = str(exc)
        return desired

    def _run(self) -> None:
        """轻量租约循环；状态不变时不会重复写串口。"""

        try:
            while not self._stop_event.is_set():
                self.tick()
                self._wake_event.wait(timeout=0.05)
                self._wake_event.clear()
        finally:
            try:
                self.controller.all_off()
            except Exception as exc:
                self.last_error = str(exc)
            finally:
                with self._lock:
                    self._last_outputs = (False, False, False, False)
                    self._effective_severity = None
                    self._reset_buzzer_cycle_locked()
                    self._thread = None

    def _ensure_thread_locked(self) -> None:
        """幂等启动唯一 pattern 线程；`/off` 后的新事故可以重新启动。"""

        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = threading.Thread(target=self._run, name="alarm-light-pattern", daemon=True)
        self._thread.start()

    def _expire_locked(self, now_value: float) -> None:
        """清理到期事故及其单次蜂鸣期限，实现 Server/网络失联自动关闭。"""

        expired_ids = [
            incident_id
            for incident_id, lease in self._leases.items()
            if lease.expires_at <= now_value
        ]
        for incident_id in expired_ids:
            self._leases.pop(incident_id, None)
            self._one_shot_buzz_deadlines.pop(incident_id, None)
        expired_buzzers = [
            incident_id
            for incident_id, deadline in self._one_shot_buzz_deadlines.items()
            if deadline <= now_value
        ]
        for incident_id in expired_buzzers:
            self._one_shot_buzz_deadlines.pop(incident_id, None)

    def _periodic_buzzer_on_locked(self, now_value: float, severity: str | None) -> bool:
        """按当前最高等级计算事故蜂鸣周期；刷新和同级并发不会改变周期起点。"""

        has_periodic_lease = severity is not None and any(
            lease.periodic_buzzer and lease.severity == severity
            for lease in self._leases.values()
        )
        if severity is None or not has_periodic_lease:
            self._reset_buzzer_cycle_locked()
            return False

        # 只有最高等级发生变化，或此前只有旧单次租约时，才立即启动一个新等级周期。
        if self._buzzer_cycle_severity != severity or self._buzzer_cycle_started_at is None:
            self._buzzer_cycle_severity = severity
            self._buzzer_cycle_started_at = now_value

        profile = self.config.profile(severity)
        cycle_seconds = profile.buzzer_seconds + profile.buzzer_pause_seconds
        if profile.buzzer_seconds <= 0 or cycle_seconds <= 0:
            return False
        elapsed_seconds = max(0.0, now_value - self._buzzer_cycle_started_at)
        return elapsed_seconds % cycle_seconds < profile.buzzer_seconds

    def _reset_buzzer_cycle_locked(self) -> None:
        """清除周期相位；最后租约结束、`/off` 或等级不再周期蜂鸣时调用。"""

        self._buzzer_cycle_severity = None
        self._buzzer_cycle_started_at = None

    def _select_effective_locked(self) -> str | None:
        """并发事故始终选择当前未过期的最高等级。"""

        if not self._leases:
            return None
        return max(
            (lease.severity for lease in self._leases.values()),
            key=self._SEVERITY_ORDER.__getitem__,
        )
