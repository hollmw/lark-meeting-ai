@echo off
echo ============================================================
echo  Meeting AI — Build EXE
echo ============================================================
echo.
echo This will create dist\MeetingAI\MeetingAI.exe
echo First build takes 10-20 minutes (large ML dependencies).
echo.

set "SCRIPT_DIR=%~dp0"
set "VENV=%SCRIPT_DIR%venv\Scripts"

:: Verify venv exists
if not exist "%VENV%\python.exe" (
    echo ERROR: No virtual environment found at .venv\
    echo Run:  python -m venv venv  then pip install -r requirements.txt
    pause & exit /b 1
)

:: Install / upgrade PyInstaller inside the venv
echo [1/3] Installing PyInstaller...
"%VENV%\pip" install pyinstaller --quiet
if errorlevel 1 ( echo PyInstaller install failed & pause & exit /b 1 )

:: Kill any running instance before cleaning
echo [2/3] Stopping any running MeetingAI instance...
taskkill /f /im MeetingAI.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: Clean previous build
echo       Cleaning previous build...
if exist "%SCRIPT_DIR%dist\MeetingAI" rmdir /s /q "%SCRIPT_DIR%dist\MeetingAI"
if exist "%SCRIPT_DIR%build"          rmdir /s /q "%SCRIPT_DIR%build"

:: Build
echo [3/3] Building (this will take a while)...
cd /d "%SCRIPT_DIR%"
"%VENV%\pyinstaller" meeting_ai.spec --noconfirm
if errorlevel 1 ( echo BUILD FAILED & pause & exit /b 1 )

:: Copy config template next to the exe (without secrets)
echo.
echo Copying config template...
if not exist "%SCRIPT_DIR%dist\MeetingAI\config.yaml" (
    copy "%SCRIPT_DIR%config.yaml" "%SCRIPT_DIR%dist\MeetingAI\config.yaml" >nul
)

echo.
echo ============================================================
echo  Build complete!
echo.
echo  Folder:  dist\MeetingAI\
echo  Run:     dist\MeetingAI\MeetingAI.exe
echo.
echo  To share with coworkers:
echo    1. Edit dist\MeetingAI\config.yaml (add Lark credentials)
echo    2. Zip the entire dist\MeetingAI\ folder
echo    3. They unzip and double-click MeetingAI.exe
echo    4. First run downloads AI model weights (~1.5 GB)
echo ============================================================
pause
