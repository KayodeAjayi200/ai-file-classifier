@echo off
echo ============================================================
echo  AI File Classifier - Setup
echo ============================================================
echo.

echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed.
    pause & exit /b 1
)

echo.
echo [2/3] Checking Ollama...
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
    echo [3/3] Pulling recommended model (qwen2.5-vl:7b)...
    echo This may take several minutes on first run.
    ollama pull qwen2.5-vl:7b
)

echo.
echo ============================================================
echo  Setup complete!
echo.
echo  Start the web app:
echo    python search.py
echo.
echo  Then open in your browser:
echo    Desktop:  http://localhost:5050
echo    Mobile:   http://YOUR_PC_IP:5050/mobile
echo    Admin:    http://localhost:5050/admin
echo ============================================================
pause
