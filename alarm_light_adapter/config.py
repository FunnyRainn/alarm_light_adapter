from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


_ALLOWED_LIGHTS = {"red", "yellow", "green"}
_SEVERITIES = ("low", "medium", "high")


@dataclass(frozen=True)
class AlarmProfile:
    """单个严重等级对应的灯光通道和首次蜂鸣时长。"""

    lights: tuple[str, ...]
    buzzer_seconds: float

    @classmethod
    def from_mapping(cls, data: dict[str, Any], fallback: "AlarmProfile") -> "AlarmProfile":
        """解析一个等级模板；显式非法通道必须报错，不能静默点错灯。"""

        raw_lights = data.get("lights", fallback.lights)
        if not isinstance(raw_lights, (list, tuple)):
            raise ValueError("alarm profile lights must be a list")
        lights = tuple(str(item or "").strip().lower() for item in raw_lights)
        if not lights or any(item not in _ALLOWED_LIGHTS for item in lights):
            raise ValueError(f"invalid alarm profile lights: {raw_lights}")
        buzzer_seconds = max(0.0, float(data.get("buzzer_seconds", fallback.buzzer_seconds)))
        return cls(lights=tuple(dict.fromkeys(lights)), buzzer_seconds=buzzer_seconds)


def _default_profiles() -> dict[str, AlarmProfile]:
    """返回独立字典，避免不同配置实例共享可变容器。"""

    return {
        "low": AlarmProfile(lights=("yellow",), buzzer_seconds=0.5),
        "medium": AlarmProfile(lights=("yellow", "red"), buzzer_seconds=1.0),
        "high": AlarmProfile(lights=("yellow", "red", "green"), buzzer_seconds=2.0),
    }


@dataclass(frozen=True)
class AdapterConfig:
    host: str = "0.0.0.0"
    port: int = 18110
    serial_port: str = "COM8"
    baudrate: int = 9600
    alarm_duration_seconds: float = 3.0
    # 事故租约默认三秒；Server 每秒 refresh，网络失联后自动到期关闭。
    lease_seconds: float = 3.0
    flash_on_ms: int = 250
    flash_off_ms: int = 250
    read_timeout_seconds: float = 0.5
    write_timeout_seconds: float = 0.5
    profiles: dict[str, AlarmProfile] = field(default_factory=_default_profiles)

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
        defaults = _default_profiles()
        raw_profiles = data.get("profiles", {})
        if raw_profiles is None:
            raw_profiles = {}
        if not isinstance(raw_profiles, dict):
            raise ValueError("profiles must be a JSON object")
        profiles: dict[str, AlarmProfile] = {}
        for severity in _SEVERITIES:
            raw_profile = raw_profiles.get(severity, {})
            if not isinstance(raw_profile, dict):
                raise ValueError(f"profile {severity} must be a JSON object")
            profiles[severity] = AlarmProfile.from_mapping(raw_profile, defaults[severity])
        legacy_duration = max(0.1, float(data.get("alarm_duration_seconds") or 3.0))
        return cls(
            host=str(data.get("host") or "0.0.0.0"),
            port=max(1, int(data.get("port") or 18110)),
            serial_port=str(data.get("serial_port") or "COM8").strip() or "COM8",
            baudrate=max(1, int(data.get("baudrate") or 9600)),
            alarm_duration_seconds=legacy_duration,
            lease_seconds=max(0.1, float(data.get("lease_seconds") or legacy_duration)),
            flash_on_ms=max(50, int(data.get("flash_on_ms") or 250)),
            flash_off_ms=max(50, int(data.get("flash_off_ms") or 250)),
            read_timeout_seconds=max(0.1, float(data.get("read_timeout_seconds") or 0.5)),
            write_timeout_seconds=max(0.1, float(data.get("write_timeout_seconds") or 0.5)),
            profiles=profiles,
        )

    def profile(self, severity: str) -> AlarmProfile:
        """按严格 low/medium/high 返回模板，防止未知等级误落到现场输出。"""

        normalized = str(severity or "").strip().lower()
        if normalized not in _SEVERITIES:
            raise ValueError(f"unsupported alarm severity: {severity}")
        return self.profiles[normalized]

    def snapshot(self) -> dict[str, Any]:
        """生成可 JSON 序列化的配置副本，供健康接口展示。"""

        return asdict(self)
