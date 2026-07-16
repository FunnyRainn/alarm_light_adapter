from __future__ import annotations

import unittest

from alarm_light_adapter.config import AdapterConfig
from alarm_light_adapter.controller import AlarmPatternRunner
from alarm_light_adapter.server import AlarmPayload, dispatch_alarm_payload


class _FakeClock:
    """由测试显式推进的单调时钟，避免真实 sleep 让租约测试抖动。"""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class _RecordingController:
    """记录最终线圈组合，不访问本机 COM 口。"""

    def __init__(self) -> None:
        self.outputs: list[tuple[bool, bool, bool, bool]] = []
        self.off_calls = 0

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
        self.off_calls += 1
        self.outputs.append((False, False, False, False))

    def health_check(self) -> dict:
        return {"serial_ok": True}

    def config_snapshot(self) -> dict:
        return {"serial_port": "FAKE"}


class _RecordingRunner:
    """HTTP 契约测试替身，只记录服务端解析后的动作。"""

    def __init__(self) -> None:
        self.actions: list[tuple[str, str, str]] = []
        self.legacy_calls: list[str] = []
        self.stop_calls = 0

    def apply(self, *, incident_id: str, severity: str, action: str) -> None:
        self.actions.append((incident_id, severity, action))

    def trigger(self, *, incident_id: str = "legacy") -> None:
        self.legacy_calls.append(incident_id)

    def stop(self) -> None:
        self.stop_calls += 1

    def is_active(self) -> bool:
        return bool(self.actions or self.legacy_calls)

    def status(self) -> dict:
        return {"active": self.is_active(), "effective_severity": "medium"}


class AlarmLeaseTests(unittest.TestCase):
    """验证事故租约、最高等级仲裁和蜂鸣去重。"""

    def setUp(self) -> None:
        self.clock = _FakeClock()
        self.controller = _RecordingController()
        self.config = AdapterConfig.from_mapping({"lease_seconds": 3.0})
        self.runner = AlarmPatternRunner(
            self.controller,
            self.config,
            clock=self.clock,
            auto_start=False,
        )

    def test_profiles_and_lease_defaults_are_stable(self) -> None:
        self.assertEqual(self.config.lease_seconds, 3.0)
        self.assertEqual(self.config.profile("low").lights, ("yellow",))
        self.assertEqual(self.config.profile("low").buzzer_seconds, 0.5)
        self.assertEqual(self.config.profile("low").buzzer_pause_seconds, 2.0)
        self.assertEqual(self.config.profile("medium").lights, ("yellow", "red"))
        self.assertEqual(self.config.profile("medium").buzzer_seconds, 1.0)
        self.assertEqual(self.config.profile("medium").buzzer_pause_seconds, 2.0)
        self.assertEqual(self.config.profile("high").lights, ("yellow", "red", "green"))
        self.assertEqual(self.config.profile("high").buzzer_seconds, 2.0)
        self.assertEqual(self.config.profile("high").buzzer_pause_seconds, 2.0)

    def test_each_incident_severity_repeats_its_buzzer_template(self) -> None:
        cases = {
            "low": (0.5, 2.5),
            "medium": (1.0, 3.0),
            "high": (2.0, 4.0),
        }
        for severity, (on_seconds, cycle_seconds) in cases.items():
            with self.subTest(severity=severity):
                clock = _FakeClock()
                controller = _RecordingController()
                runner = AlarmPatternRunner(
                    controller,
                    AdapterConfig.from_mapping({"lease_seconds": 20.0}),
                    clock=clock,
                    auto_start=False,
                )
                runner.apply(incident_id=f"incident-{severity}", severity=severity, action="raise")
                self.assertTrue(runner.tick()[3])
                clock.value = on_seconds
                self.assertFalse(runner.tick()[3])
                clock.value = cycle_seconds - 0.01
                self.assertFalse(runner.tick()[3])
                clock.value = cycle_seconds
                self.assertTrue(runner.tick()[3])

    def test_refresh_and_same_severity_incident_do_not_reset_cycle(self) -> None:
        self.runner.apply(incident_id="incident-medium-1", severity="medium", action="raise")
        self.assertTrue(self.runner.tick()[3])

        # 0.8 秒刷新后，周期仍从最初的 0 秒计算；1 秒时应进入停顿段。
        self.clock.value = 0.8
        self.runner.apply(incident_id="incident-medium-1", severity="medium", action="refresh")
        self.clock.value = 1.0
        self.assertFalse(self.runner.tick()[3])

        # 同等级新事故也不能把停顿段重新拉回蜂鸣段。
        self.clock.value = 1.2
        self.runner.apply(incident_id="incident-medium-2", severity="medium", action="raise")
        self.assertFalse(self.runner.tick()[3])

        # 第二次刷新仍只续租；完整 1+2 秒周期后才再次蜂鸣。
        self.clock.value = 2.0
        self.runner.apply(incident_id="incident-medium-1", severity="medium", action="refresh")
        self.clock.value = 3.0
        self.assertTrue(self.runner.tick()[3])

    def test_severity_change_switches_template_immediately(self) -> None:
        self.runner.apply(incident_id="incident-low", severity="low", action="raise")
        self.assertTrue(self.runner.tick()[3])
        self.clock.value = 0.7
        self.assertFalse(self.runner.tick()[3])

        # 高等级接管时立即从高等级 2 秒蜂鸣段开始。
        self.runner.apply(incident_id="incident-high", severity="high", action="raise")
        high_outputs = self.runner.tick()
        self.assertEqual(self.runner.status()["effective_severity"], "high")
        self.assertEqual(high_outputs, (True, True, True, True))

        # 高等级解除后立即切回低等级模板，而不是延续高等级的旧相位。
        self.clock.value = 1.0
        self.runner.apply(incident_id="incident-low", severity="low", action="refresh")
        self.runner.apply(incident_id="incident-high", severity="high", action="resolve")
        low_outputs = self.runner.tick()
        self.assertEqual(self.runner.status()["effective_severity"], "low")
        self.assertTrue(low_outputs[3])

    def test_last_lease_expiry_and_stop_turn_everything_off_and_reset_cycle(self) -> None:
        self.runner.apply(incident_id="incident-low", severity="low", action="raise")
        self.runner.tick()
        self.clock.value = 3.0
        self.assertEqual(self.runner.tick(), (False, False, False, False))
        self.assertFalse(self.runner.is_active())

        # `/off` 使用 stop；之后同一 runner 接受新事故时必须从新的蜂鸣周期开始。
        self.runner.stop()
        self.assertEqual(self.controller.outputs[-1], (False, False, False, False))
        self.clock.value = 4.0
        self.runner.apply(incident_id="incident-medium", severity="medium", action="raise")
        self.assertTrue(self.runner.tick()[3])

    def test_legacy_trigger_remains_one_shot_instead_of_periodic(self) -> None:
        runner = AlarmPatternRunner(
            self.controller,
            AdapterConfig.from_mapping({"lease_seconds": 10.0}),
            clock=self.clock,
            auto_start=False,
        )
        runner.trigger(incident_id="legacy:station_1")
        self.assertTrue(runner.tick()[3])
        self.clock.value = 1.0
        self.assertFalse(runner.tick()[3])
        self.clock.value = 3.0
        self.assertFalse(runner.tick()[3])
        self.assertTrue(runner.is_active())

    def test_invalid_severity_or_action_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.runner.apply(incident_id="incident-1", severity="critical", action="raise")
        with self.assertRaises(ValueError):
            self.runner.apply(incident_id="incident-1", severity="high", action="restart")


class AlarmApiCompatibilityTests(unittest.TestCase):
    """验证新事故载荷和旧单次载荷共用原 `/alarm` 地址。"""

    def setUp(self) -> None:
        self.runner = _RecordingRunner()

    def test_new_incident_actions_are_forwarded_without_legacy_trigger(self) -> None:
        for action in ("raise", "refresh", "resolve"):
            response = dispatch_alarm_payload(AlarmPayload(
                station_id="station_3",
                incident_id="incident-3",
                severity="high",
                action=action,
                alarm={"code": "B3"},
            ), self.runner)
            self.assertTrue(response["ok"])
        self.assertEqual(self.runner.actions, [
            ("incident-3", "high", "raise"),
            ("incident-3", "high", "refresh"),
            ("incident-3", "high", "resolve"),
        ])
        self.assertEqual(self.runner.legacy_calls, [])

    def test_old_payload_remains_medium_single_trigger(self) -> None:
        response = dispatch_alarm_payload(AlarmPayload(
            station_id="station_1",
            alarm={"code": "LEGACY"},
        ), self.runner)
        self.assertEqual(response["severity"], "medium")
        self.assertEqual(response["action"], "legacy")
        self.assertEqual(self.runner.actions, [])
        self.assertEqual(self.runner.legacy_calls, ["legacy:station_1"])

    def test_partial_incident_payload_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            dispatch_alarm_payload(AlarmPayload(
                incident_id="incident-1",
                severity="high",
            ), self.runner)


if __name__ == "__main__":
    unittest.main()
