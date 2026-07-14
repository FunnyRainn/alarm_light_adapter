# Alarm Light Adapter

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
