# Alarm Light Adapter v0.2.1

Small HTTP-to-serial adapter for the USB tower light and buzzer.

The main `act_server` already sends light alarm events by HTTP POST to a
configured `light_endpoint`. This adapter receives that HTTP request and drives
the CH340 USB serial alarm device with Modbus RTU commands.

## Hardware

- Serial port: configured in `config.json`, default `COM8`
- Baud rate: `9600`
- Data bits: `8`
- Parity: none
- Stop bits: `1`

Coil mapping:

- `0000`: red light
- `0001`: yellow light
- `0002`: green light
- `0003`: buzzer

## API

- `GET /health`
- `POST /test`
- `POST /alarm`
- `POST /off`

新版 `/alarm` 支持按事故租约控制：

```json
{
  "station_id": "station_3",
  "incident_id": "uuid",
  "severity": "high",
  "action": "raise",
  "alarm": {"code": "B3"}
}
```

`action` 可为 `raise / refresh / resolve`。每个事故默认租约 3 秒；Server
应每秒发送一次 `refresh`。网络或 Server 失联后不再续租，适配器会自动关闭。
并发事故始终采用未过期的最高等级；等级升降时立即切换新等级模板。同等级新增事故和
Server 的 `refresh` 只延长租约，不会重置当前蜂鸣周期。

- `low`：黄灯闪烁，蜂鸣 0.5 秒、停顿 2 秒，事故持续时循环。
- `medium`：黄灯、红灯闪烁，蜂鸣 1 秒、停顿 2 秒，事故持续时循环。
- `high`：黄灯、红灯、绿灯闪烁，蜂鸣 2 秒、停顿 2 秒，事故持续时循环。

最后一个事故解除、租约过期或调用 `/off` 后会立即关闭并重置周期。旧载荷不含
`incident_id/severity/action` 时仍按中度单次触发处理，`/test` 也保持单次蜂鸣，
二者都不会自动进入循环。

For `act_web` station configuration, enable light alarm and use:

```text
http://127.0.0.1:18110/alarm
```

When the adapter runs on a station terminal instead of the central server, use:

```text
http://<station-terminal-ip>:18110/alarm
```

## Run

Use the dedicated conda environment for this adapter. Do not reuse the heavy
CUDA/Torch inference environment.

```cmd
conda activate alarm_light_py310
python -m alarm_light_adapter.server --config config.json
```

Or run the packaged command:

```cmd
run_adapter.cmd
```

The `run_*.cmd` scripts require a conda environment named
`alarm_light_py310`, with `conda activate` available in CMD. They intentionally
do not fall back to other Python installs; if this environment is missing, the
scripts stop with an error.

To use a different conda environment name, set:

```cmd
set ALARM_LIGHT_CONDA_ENV=your_env_name
run_adapter.cmd
```

## Local Test

```powershell
curl.exe http://127.0.0.1:18110/health
curl.exe -X POST http://127.0.0.1:18110/test
curl.exe -X POST http://127.0.0.1:18110/off
```

If the target machine maps the CH340 device to a different COM port, edit
`config.json`.

## Identify COM Ports

Run the interactive identifier when several identical alarm devices are plugged
into one machine:

```cmd
run_identify_ports.cmd
```

The script tests one COM at a time, flashes and buzzes for 0.5 seconds, then
waits for Enter before moving to the next port. It only prints the COM value to
record in each adapter `config.json`; it does not edit or generate config files.
