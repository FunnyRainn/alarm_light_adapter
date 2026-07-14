from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AdapterConfig:
    host: str = "0.0.0.0"
    port: int = 18110
    serial_port: str = "COM8"
    baudrate: int = 9600
    alarm_duration_seconds: float = 3.0
    flash_on_ms: int = 250
    flash_off_ms: int = 250
    read_timeout_seconds: float = 0.5
    write_timeout_seconds: float = 0.5

    @classmethod
    def from_file(cls, path: str | Path) -> "AdapterConfig":
        config_path = Path(path)
        if not config_path.exists():
            return cls()
        data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"Adapter config must be a JSON object: {config_path}")
        return cls.from_mapping(data)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "AdapterConfig":
        return cls(
            host=str(data.get("host") or "0.0.0.0"),
            port=max(1, int(data.get("port") or 18110)),
            serial_port=str(data.get("serial_port") or "COM8").strip() or "COM8",
            baudrate=max(1, int(data.get("baudrate") or 9600)),
            alarm_duration_seconds=max(0.1, float(data.get("alarm_duration_seconds") or 3.0)),
            flash_on_ms=max(50, int(data.get("flash_on_ms") or 250)),
            flash_off_ms=max(50, int(data.get("flash_off_ms") or 250)),
            read_timeout_seconds=max(0.1, float(data.get("read_timeout_seconds") or 0.5)),
            write_timeout_seconds=max(0.1, float(data.get("write_timeout_seconds") or 0.5)),
        )
