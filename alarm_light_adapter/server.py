from __future__ import annotations

import argparse
import atexit
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .config import AdapterConfig
from .controller import AlarmPatternRunner, SerialLightController


class AlarmPayload(BaseModel):
    station_id: str = ""
    station_name: str = ""
    incident_id: str = ""
    severity: Literal["low", "medium", "high"] | None = None
    action: Literal["raise", "refresh", "resolve"] | None = None
    alarm: dict[str, Any] = Field(default_factory=dict)


def dispatch_alarm_payload(payload: AlarmPayload, runner: Any) -> dict[str, Any]:
    """把新事故载荷或旧兼容载荷分发给同一个租约执行器。"""

    has_incident_field = bool(payload.incident_id or payload.severity is not None or payload.action is not None)
    if has_incident_field:
        if not payload.incident_id or payload.severity is None or payload.action is None:
            raise ValueError("incident_id, severity and action must be provided together")
        runner.apply(
            incident_id=payload.incident_id,
            severity=payload.severity,
            action=payload.action,
        )
        action = payload.action
        severity = payload.severity
        incident_id = payload.incident_id
    else:
        # 旧 Server 不携带事故字段，继续按中度单次触发；工位键保持同租约内重复调用只续期。
        incident_id = f"legacy:{payload.station_id or 'default'}"
        runner.trigger(incident_id=incident_id)
        action = "legacy"
        severity = "medium"
    return {
        "ok": True,
        "triggered": action in {"raise", "legacy"},
        "active": runner.is_active(),
        "station_id": payload.station_id,
        "incident_id": incident_id,
        "severity": severity,
        "action": action,
    }


def create_app(
    config: AdapterConfig | None = None,
    *,
    controller: Any | None = None,
    runner: Any | None = None,
) -> FastAPI:
    cfg = config or AdapterConfig()
    active_controller = controller or SerialLightController(cfg)
    active_runner = runner or AlarmPatternRunner(active_controller, cfg)
    app = FastAPI(title="alarm_light_adapter", version=__version__)
    app.state.config = cfg
    app.state.controller = active_controller
    app.state.runner = active_runner

    @app.on_event("startup")
    def on_startup() -> None:
        try:
            active_controller.all_off()
        except Exception:
            pass

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        active_runner.stop()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "config": active_controller.config_snapshot(),
            "serial": active_controller.health_check(),
            "alarm": active_runner.status(),
        }

    @app.post("/alarm")
    def alarm(payload: AlarmPayload | None = None) -> dict[str, Any]:
        try:
            return dispatch_alarm_payload(payload or AlarmPayload(), active_runner)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/test")
    def test_alarm() -> dict[str, Any]:
        incident_id = f"test:{uuid4()}"
        active_runner.trigger(incident_id=incident_id)
        return {"ok": True, "triggered": True, "active": active_runner.is_active()}

    @app.post("/off")
    def off() -> dict[str, Any]:
        active_runner.stop()
        return {"ok": True, "active": active_runner.is_active()}

    atexit.register(active_runner.stop)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="HTTP-to-serial alarm light adapter")
    parser.add_argument("--config", default="config.json", help="Path to adapter JSON config")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = AdapterConfig.from_file(Path(args.config))
    uvicorn.run(
        create_app(config),
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
