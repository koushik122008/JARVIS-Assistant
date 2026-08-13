@echo off
rem ── MARK XLIX — Start JARVIS with the correct Python (.venv) ─────────────
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
) else (
    echo [ERROR] .venv not found. Create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\python -m pip install -r requirements.txt
    pause
)
