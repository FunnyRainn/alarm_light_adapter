from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alarm_light_adapter.config import AdapterConfig
from alarm_light_adapter.controller import SerialLightController


DEFAULT_BAUDRATE = 9600
DEFAULT_DURATION_SECONDS = 0.5
CH340_VID_PID = "VID_1A86&PID_7523"


@dataclass(frozen=True)
class PortCandidate:
    device: str
    description: str = ""
    hwid: str = ""

    def display(self) -> str:
        parts = [self.device]
        if self.description:
            parts.append(self.description)
        if self.hwid:
            parts.append(self.hwid)
        return " / ".join(parts)


def discover_ports(include_all: bool) -> list[PortCandidate]:
    try:
        from serial.tools import list_ports  # type: ignore
    except Exception:
        return [PortCandidate(f"COM{i}") for i in range(1, 257)] if include_all else []

    ports = []
    for item in list_ports.comports():
        hwid = str(getattr(item, "hwid", "") or "")
        desc = str(getattr(item, "description", "") or "")
        ports.append(PortCandidate(str(item.device), desc, hwid))

    ch340_ports = [
        port for port in ports
        if CH340_VID_PID in port.hwid.upper() or "CH340" in port.description.upper()
    ]
    if ch340_ports and not include_all:
        return sorted(ch340_ports, key=port_sort_key)
    return sorted(ports, key=port_sort_key)


def port_sort_key(port: PortCandidate) -> tuple[int, str]:
    text = port.device.upper()
    if text.startswith("COM") and text[3:].isdigit():
        return int(text[3:]), text
    return 9999, text


def make_config(port: str, baudrate: int, duration_seconds: float) -> AdapterConfig:
    return AdapterConfig(
        serial_port=port,
        baudrate=baudrate,
        alarm_duration_seconds=duration_seconds,
        read_timeout_seconds=0.3,
        write_timeout_seconds=0.3,
    )


def flash_once(port: str, baudrate: int, duration_seconds: float) -> tuple[bool, str]:
    controller = SerialLightController(make_config(port, baudrate, duration_seconds))
    try:
        controller.all_on()
        time.sleep(duration_seconds)
        controller.all_off()
        return True, ""
    except Exception as exc:
        try:
            controller.all_off()
        except Exception:
            pass
        return False, str(exc)


def wait_for_next() -> bool:
    answer = input("按 Enter 测试下一个 COM；输入 q 后回车退出：").strip().lower()
    return answer != "q"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="逐个短闪声光报警器，人工识别报警器对应的 COM 口。")
    parser.add_argument("--port", action="append", dest="ports", help="只测试指定 COM 口，可重复传入，例如 --port COM8 --port COM9")
    parser.add_argument("--include-all", action="store_true", help="列出全部串口；默认优先只测试 CH340 报警器候选")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE, help="串口波特率，默认 9600")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS, help="每个 COM 短闪短响秒数，默认 0.5")
    parser.add_argument("--list-only", action="store_true", help="只列出候选 COM，不执行短闪测试")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    duration = max(0.1, float(args.duration))
    if args.ports:
        candidates = [PortCandidate(port.strip().upper()) for port in args.ports if port.strip()]
    else:
        candidates = discover_ports(include_all=args.include_all)

    if not candidates:
        print("未发现 CH340 报警器候选 COM。")
        print("可先确认设备已插入，或使用 --include-all 列出全部串口后逐个测试。")
        return 1

    if not args.ports and not args.include_all:
        has_ch340 = any(
            CH340_VID_PID in candidate.hwid.upper() or "CH340" in candidate.description.upper()
            for candidate in candidates
        )
        if not has_ch340:
            print("未发现 CH340 标识的报警器串口，已回退列出全部已枚举串口。")
            print("如需减少测试范围，可用 --port COM8 指定单个端口，或重复传入多个 --port。")
            print()

    print("候选 COM：")
    for idx, candidate in enumerate(candidates, start=1):
        print(f"  {idx}. {candidate.display()}")
    if args.list_only:
        return 0

    print()
    print("测试说明：每次只短闪短响一个 COM，观察是哪一个工位报警器响应。")
    print("请人工记录到对应 adapter 的 config.json，例如：")
    print('  "serial_port": "COM8"')
    print("三工位三设备时，还需要给三个 adapter 配不同 HTTP port，例如 18111/18112/18113。")
    print()

    for idx, candidate in enumerate(candidates, start=1):
        print("=" * 72)
        print(f"[{idx}/{len(candidates)}] 正在测试：{candidate.display()}")
        print(f"如果当前有报警器闪/响，请记录：对应工位 config.json -> \"serial_port\": \"{candidate.device}\"")
        ok, error = flash_once(candidate.device, int(args.baudrate), duration)
        if ok:
            print(f"{candidate.device} 已短闪短响 {duration:.1f} 秒，并已发送关闭命令。")
        else:
            print(f"{candidate.device} 测试失败：{error}")
            print("常见原因：不是报警器串口、设备未插好、厂家软件或 adapter 正在占用该 COM。")
        if idx < len(candidates) and not wait_for_next():
            print("已退出。")
            return 0

    print("=" * 72)
    print("测试完成。请把观察到的 COM 写入各工位 adapter 的 config.json。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
