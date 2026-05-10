@echo off
setlocal
echo.
echo ============================================================
echo  AI File Classifier - Build Installer
echo ============================================================
echo.
echo This script builds the Windows installer (.exe) using Inno Setup.
echo Inno Setup must be installed first: https://jrsoftware.org/isinfo.php
echo.

:: Find Inno Setup compiler (checks Program Files and user-local install)
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe"       set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

:: Check PATH too
where ISCC.exe >nul 2>&1 && set ISCC=ISCC.exe

if "%ISCC%"=="" (
    echo ERROR: Inno Setup not found.
    echo Please install Inno Setup 6 from: https://jrsoftware.org/isdl.php
    echo Then re-run this script.
    pause & exit /b 1
)

echo Found Inno Setup: %ISCC%
echo.
echo Building installer...
%ISCC% "%~dp0installer\app.iss"

if %errorlevel% neq 0 (
    echo.
    echo ERROR: Build failed. Check the output above.
    pause & exit /b 1
)

echo.
echo ============================================================
echo  SUCCESS!
echo.
echo  Installer created:
echo    installer\dist\AIFileClassifier-Setup.exe
echo.
echo  Upload this file to a GitHub Release so others can download it.
echo ============================================================
pause
