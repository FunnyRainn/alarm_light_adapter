@echo off
setlocal
cd /d "%~dp0"
if "%ALARM_LIGHT_CONDA_ENV%"=="" set "ALARM_LIGHT_CONDA_ENV=alarm_light_py310"
call conda activate "%ALARM_LIGHT_CONDA_ENV%"
if errorlevel 1 (
    echo Failed to activate conda env "%ALARM_LIGHT_CONDA_ENV%".
    echo Please create this environment and make sure "conda activate" works in CMD.
    exit /b 1
)
python scripts\identify_alarm_ports.py %*
