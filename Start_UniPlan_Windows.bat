@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if %errorlevel%==0 (
  set "PYTHON_CMD=py -3"
) else (
  set "PYTHON_CMD=python"
)
%PYTHON_CMD% --version >nul 2>&1
if errorlevel 1 (
  echo Python 3 is not installed. Install it from https://www.python.org/downloads/
  echo During setup, enable "Add Python to PATH".
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating your local Python environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 goto :failed
)
echo Installing requirements (Internet needed on first run)...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed
echo.
echo Starting UniPlan. Keep this window open while using the application.
echo If your browser does not open, visit http://127.0.0.1:8000
echo.
".venv\Scripts\python.exe" app.py --open-browser
pause
exit /b 0
:failed
echo Setup failed. Please review the error above.
pause
exit /b 1
