from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alarm_light_adapter.config import AdapterConfig
from alarm_light_adapter.modbus import write_single_coil_frame


def check_modbus_frames() -> None:
    expected = {
        (0, True): "01050000ff008c3a",
        (1, True): "01050001ff00ddfa",
        (2, True): "01050002ff002dfa",
        (3, True): "01050003ff007c3a",
        (0, False): "010500000000cdca",
        (1, False): "0105000100009c0a",
        (2, False): "0105000200006c0a",
        (3, False): "0105000300003dca",
    }
    for (coil, enabled), hex_value in expected.items():
        actual = write_single_coil_frame(1, coil, enabled).hex()
        if actual != hex_value:
            raise AssertionError(f"coil={coil} enabled={enabled}: {actual} != {hex_value}")


def check_config_defaults() -> None:
    cfg = AdapterConfig.from_mapping({})
    if cfg.port != 18110:
        raise AssertionError("default port should be 18110")
    if cfg.serial_port != "COM8":
        raise AssertionError("default serial port should be COM8")
    if cfg.baudrate != 9600:
        raise AssertionError("default baudrate should be 9600")
    if cfg.lease_seconds != 3.0:
        raise AssertionError("default incident lease should be 3 seconds")
    if cfg.profile("low").lights != ("yellow",):
        raise AssertionError("low profile should flash yellow")
    if cfg.profile("high").buzzer_seconds != 2.0:
        raise AssertionError("high profile should buzz for 2 seconds")
    if any(cfg.profile(severity).buzzer_pause_seconds != 2.0 for severity in ("low", "medium", "high")):
        raise AssertionError("all incident profiles should pause for 2 seconds")


def main() -> None:
    check_modbus_frames()
    check_config_defaults()
    print("alarm_light_adapter checks passed")


if __name__ == "__main__":
    main()
