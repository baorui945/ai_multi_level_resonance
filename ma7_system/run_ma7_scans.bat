@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set PYTHONUNBUFFERED=1

set POOL=--universe hs300
set LABEL=HS300
if not "%~1"=="" set POOL=--codes %~1
if not "%~1"=="" set LABEL=%~1
set D_RECENT=5
set W_RECENT=3

title MA7 Upthrow One-click Scan - %LABEL%

echo ============================================================
echo   MA7 Upthrow One-click Scan (daily / weekly / resonance)
echo   Pool : %LABEL%
echo   Start: %DATE% %TIME%
echo   Dir  : %CD%
echo ============================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
    pause
    exit /b 1
)

set FAIL=0

echo [1/3] Daily upthrow scan  (D3 confirm, D4 open)
echo ------------------------------------------------------------
python ma7_scan_close3.py %POOL% --recent %D_RECENT%
if errorlevel 1 set FAIL=1
echo.

echo [2/3] Weekly upthrow scan  (W-FRI, closed weeks only)
echo ------------------------------------------------------------
python ma7_scan_week.py %POOL% --recent %W_RECENT%
if errorlevel 1 set FAIL=1
echo.

echo [3/3] Daily x Weekly resonance  (entry only inside weekly segment)
echo ------------------------------------------------------------
python ma7_scan_res.py %POOL%
if errorlevel 1 set FAIL=1
echo.

echo ============================================================
if "%FAIL%"=="1" (
    echo   Done WITH ERRORS - see WARN / traceback above.
) else (
    echo   All done.
)
echo   End  : %DATE% %TIME%
echo   Out  : %CD%\output
echo          ma7_scan_close3_YYYYMMDD.csv   daily
echo          ma7_week_scan_YYYYMMDD.csv     weekly
echo          ma7_res_scan_YYYYMMDD.csv      resonance
echo ============================================================
pause
endlocal
