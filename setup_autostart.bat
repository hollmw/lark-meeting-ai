@echo off
echo ============================================
echo  Meeting AI — Setup
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SHORTCUT=%STARTUP%\MeetingAI.lnk"
set "VBS=%SCRIPT_DIR%start_hidden.vbs"

:: Check Python exists
if not exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    echo [1/3] Creating Python virtual environment...
    python -m venv "%SCRIPT_DIR%.venv"
    if errorlevel 1 (
        echo ERROR: Python not found. Install Python 3.11 first.
        pause & exit /b 1
    )
    echo [2/3] Installing dependencies...
    "%SCRIPT_DIR%.venv\Scripts\pip" install -r "%SCRIPT_DIR%requirements.txt" --quiet
) else (
    echo [1/3] Virtual environment already exists — skipping.
    echo [2/3] Dependencies already installed — skipping.
)

:: Create startup shortcut via PowerShell
echo [3/3] Adding to Windows startup...
powershell -NoProfile -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $s = $ws.CreateShortcut('%SHORTCUT%'); ^
   $s.TargetPath = 'wscript.exe'; ^
   $s.Arguments = '\"%VBS%\"'; ^
   $s.WorkingDirectory = '%SCRIPT_DIR%'; ^
   $s.Description = 'Meeting AI Tray'; ^
   $s.Save()"

echo.
echo ============================================
echo  Done! Meeting AI will start automatically
echo  every time you log in to Windows.
echo.
echo  Starting now...
echo ============================================
start "" wscript.exe "%VBS%"
