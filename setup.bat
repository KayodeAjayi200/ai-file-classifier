@echo off
echo ============================================================
echo  AI File Classifier - Setup
echo ============================================================
echo.

echo [1/4] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed.
    pause & exit /b 1
)

echo.
echo [2/4] Checking Ollama...
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% neq 0 (
    echo Ollama is NOT running. Please:
    echo   1. Install Ollama: https://ollama.com
    echo   2. Run in a separate terminal: ollama serve
    echo   3. Then re-run this script, or manually pull the model:
    echo      ollama pull qwen2.5-vl:7b
    echo.
) else (
    echo Ollama is running!
    echo.
    echo [3/4] Pulling recommended model (qwen2.5-vl:7b)...
    echo This may take several minutes on first run.
    ollama pull qwen2.5-vl:7b
)

echo.
echo [4/4] Creating Desktop shortcut...
set "DIR=%~dp0"
set "DIR=%DIR:~0,-1%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws  = New-Object -ComObject WScript.Shell; ^
   $lnk = $ws.CreateShortcut([System.IO.Path]::Combine([System.Environment]::GetFolderPath('Desktop'), 'AI File Classifier.lnk')); ^
   $lnk.TargetPath      = [System.IO.Path]::Combine($env:SystemRoot, 'System32', 'wscript.exe'); ^
   $lnk.Arguments       = '\"%DIR%\run.vbs\"'; ^
   $lnk.WorkingDirectory= '%DIR%'; ^
   $lnk.Description     = 'AI File Classifier - Local AI media manager'; ^
   if (Test-Path '%DIR%\app_icon.ico') { $lnk.IconLocation = '%DIR%\app_icon.ico' }; ^
   $lnk.Save()"

if %errorlevel% neq 0 (
    echo   Could not create shortcut automatically.
    echo   To launch manually, run:  wscript run.vbs
) else (
    echo   Shortcut created on your Desktop!
)

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  LAUNCH: Double-click "AI File Classifier" on your Desktop
echo          (or run:  pythonw launcher.py)
echo.
echo  Manual start (no tray):
echo    python search.py
echo    Then open: http://localhost:5050
echo.
echo  Mobile:  http://YOUR_PC_IP:5050/mobile
echo  Admin:   http://localhost:5050/admin
echo ============================================================
pause
