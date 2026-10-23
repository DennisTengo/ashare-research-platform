@echo off
cd /d "%~dp0"

echo ========================================
echo   A-Share Research Platform
echo ========================================
echo.
echo Starting... browser will open http://localhost:8501
echo Close this window or press Ctrl+C to stop.
echo.

rem Delay 10s so Streamlit has time to boot (ping used instead of timeout:
rem timeout needs a real console and fails under redirected input)
start "" /b cmd /c "ping -n 10 127.0.0.1 >nul & start http://localhost:8501"

".venv\Scripts\python.exe" run.py --skip-update

echo.
echo Service stopped.
pause
