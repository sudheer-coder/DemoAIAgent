@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
echo Installing requirements (first run only)...
%PY% -m pip install -q -r requirements.txt
rem Listen on the Wi-Fi too, so your phone can open the dashboard.
rem If Windows asks about the firewall, click "Allow" (Private networks).
set "HOST=0.0.0.0"
%PY% server.py
pause
