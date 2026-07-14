from __future__ import annotations

import argparse
import atexit
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel, Field

from . import __version__
from .config import AdapterConfig
from .controller import AlarmPatternRunner, SerialLightController


class AlarmPayload(BaseModel):
    station_id: str = ""
    station_name: str = ""
    alarm: dict[str, Any] = Field(default_factory=dict)


def create_app(config: AdapterConfig | None = None) -> FastAPI:
    cfg = config or AdapterConfig()
    controller = SerialLightController(cfg)
    runner = AlarmPatternRunner(controller, cfg)
    app = FastAPI(title="alarm_light_adapter", version=__version__)
    app.state.config = cfg
    app.state.controller = controller
    app.state.runner = runner

    @app.on_event("startup")
    def on_startup() -> None:
        try:
            controller.all_off()
        except Exception:
            pass

    @app.on_event("shutdown")
    def on_shutdown() -> None:
        runner.stop()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": __version__,
            "config": controller.config_snapshot(),
            "serial": controller.health_check(),
            "alarm": runner.status(),
        }

    @app.post("/alarm")
    def alarm(payload: AlarmPayload | None = None) -> dict[str, Any]:
        runner.trigger()
        return {
            "ok": True,
            "triggered": True,
            "active": runner.is_active(),
            "station_id": payload.station_id if payload else "",
        }

    @app.post("/test")
    def test_alarm() -> dict[str, Any]:
        runner.trigger()
        return {"ok": True, "triggered": True, "active": runner.is_active()}

    @app.post("/off")
    def off() -> dict[str, Any]:
        runner.stop()
        return {"ok": True, "active": runner.is_active()}

    atexit.register(runner.stop)
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
