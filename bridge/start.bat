@echo off
cd /d "%~dp0"

echo.
echo  Starting Haier AC...
echo.

:: LAN IP — uses the interface that holds the default route
for /f %%i in ('powershell -NoProfile -Command ^
  "(Get-NetRoute -DestinationPrefix 0.0.0.0/0 | Sort-Object RouteMetric | Select-Object -First 1 | Get-NetIPAddress).IPAddress"') ^
  do set LAN_IP=%%i

:: Public IP — silent if offline or times out
for /f %%i in ('powershell -NoProfile -Command ^
  "try { Invoke-RestMethod -Uri https://api.ipify.org -TimeoutSec 3 } catch { '' }"') ^
  do set PUBLIC_IP=%%i

echo  Local  ^(LAN^)   ^-^>  http://%LAN_IP%:8765
if defined PUBLIC_IP (
    echo  External       ^-^>  http://%PUBLIC_IP%:8765  ^(requires port 8765 forwarded on router^)
)
echo.

call venv\Scripts\activate
uvicorn main:app --host 0.0.0.0 --port 8765
