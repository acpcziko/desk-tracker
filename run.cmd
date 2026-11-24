@echo off
rem Starts Desk Tracker and restarts it if it ever stops. Used by the "DeskTracker" scheduled task.
cd /d "%~dp0"
if "%DESK_TRACKER_PORT%"=="" set DESK_TRACKER_PORT=8080
:loop
echo [%date% %time%] starting desk tracker on port %DESK_TRACKER_PORT% >> data\server.log
"%~dp0venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port %DESK_TRACKER_PORT% --no-access-log >> data\server.log 2>&1
echo [%date% %time%] stopped with code %errorlevel%, restarting in 10 s >> data\server.log
timeout /t 10 /nobreak > nul
goto loop
