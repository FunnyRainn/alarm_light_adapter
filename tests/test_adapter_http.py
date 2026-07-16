from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from urllib.request import Request, urlopen

import uvicorn

from alarm_light_adapter.config import AdapterConfig
from alarm_light_adapter.controller import AlarmPatternRunner
from alarm_light_adapter.server import create_app


class _FakeClock:
    """由测试显式推进的单调时钟，HTTP 验证无需等待真实蜂鸣周期。"""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class _FakeController:
    """完整模拟 HTTP 应用所需的串口控制器接口，确保测试不访问任何 COM 设备。"""

    def __init__(self) -> None:
        self.outputs: list[tuple[bool, bool, bool, bool]] = []

    def set_channels(
        self,
        *,
        red: bool = False,
        yellow: bool = False,
        green: bool = False,
        buzzer: bool = False,
    ) -> None:
        self.outputs.append((red, yellow, green, buzzer))

    def all_off(self) -> None:
        self.outputs.append((False, False, False, False))

    def health_check(self) -> dict:
        return {"serial_ok": True, "serial_port": "FAKE"}

    def config_snapshot(self) -> dict:
        return {"serial_port": "FAKE"}


def _reserve_local_port() -> int:
    """向系统申请当前空闲的回环端口，避免测试占用正式 Adapter 端口。"""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AdapterHttpIntegrationTests(unittest.TestCase):
    """通过真实 HTTP 监听验证新旧载荷分流，不连接现场串口。"""

    def setUp(self) -> None:
        self.clock = _FakeClock()
        self.controller = _FakeController()
        self.port = _reserve_local_port()
        self.config = AdapterConfig.from_mapping({
            "host": "127.0.0.1",
            "port": self.port,
            "lease_seconds": 10.0,
        })
        self.runner = AlarmPatternRunner(
            self.controller,
            self.config,
            clock=self.clock,
            auto_start=False,
        )
        app = create_app(self.config, controller=self.controller, runner=self.runner)
        uvicorn_config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="critical",
        )
        self.server = uvicorn.Server(uvicorn_config)
        self.thread = threading.Thread(
            target=self.server.run,
            name="alarm-adapter-http-test",
            daemon=True,
        )
        self.thread.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                if self._request("GET", "/health")["ok"]:
                    break
            except OSError:
                time.sleep(0.02)
        else:
            self.fail("isolated adapter HTTP server did not start")

    def tearDown(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=3.0)
        self.assertFalse(self.thread.is_alive(), "isolated adapter HTTP server leaked")

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        """发送一条本机 JSON 请求并返回解析结果。"""

        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=2.0) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_incident_cycle_and_legacy_routes_remain_isolated(self) -> None:
        raised = self._request("POST", "/alarm", {
            "station_id": "station_3",
            "incident_id": "incident-http",
            "severity": "medium",
            "action": "raise",
            "alarm": {"code": "B3"},
        })
        self.assertEqual(raised["action"], "raise")
        self.assertTrue(self.runner.tick()[3])

        # HTTP refresh 只续租；到 1 秒边界进入停顿，到 3 秒边界恢复蜂鸣。
        self.clock.value = 0.8
        self._request("POST", "/alarm", {
            "incident_id": "incident-http",
            "severity": "medium",
            "action": "refresh",
        })
        self.clock.value = 1.0
        self.assertFalse(self.runner.tick()[3])
        self.clock.value = 3.0
        self.assertTrue(self.runner.tick()[3])

        stopped = self._request("POST", "/off")
        self.assertFalse(stopped["active"])
        self.assertEqual(self.controller.outputs[-1], (False, False, False, False))

        # 旧 `/alarm` 仍只响一次：即使租约仍有效，到完整 3 秒周期也不会自动再响。
        self.clock.value = 10.0
        legacy = self._request("POST", "/alarm", {
            "station_id": "station_1",
            "alarm": {"code": "LEGACY"},
        })
        self.assertEqual(legacy["action"], "legacy")
        self.assertTrue(self.runner.tick()[3])
        self.clock.value = 11.0
        self.assertFalse(self.runner.tick()[3])
        self.clock.value = 13.0
        self.assertFalse(self.runner.tick()[3])
        self.assertTrue(self.runner.is_active())

        # `/test` 使用唯一旧租约，因此每次请求仍产生一次、但不会产生循环蜂鸣。
        tested = self._request("POST", "/test")
        self.assertTrue(tested["triggered"])
        self.assertTrue(self.runner.tick()[3])
        self.clock.value = 16.0
        self.assertFalse(self.runner.tick()[3])


if __name__ == "__main__":
    unittest.main()
