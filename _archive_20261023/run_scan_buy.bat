@echo off
setlocal
title Route B scan
cd /d "%~dp0"

REM ==========================================================================
REM  Route B buy-signal scan - one-click launcher
REM --------------------------------------------------------------------------
REM  Double-click    : scan ALL stocks, fresh_days <= 5, score >= 0.98
REM  With arguments  : run_scan_buy.bat --universe hs300 --fresh-days 3
REM                    (arguments are passed through to scan_buy.py)
REM  Output          : output\scanDaily\buy_alert_<YYYYMMDD>.xlsx
REM                    sheets: hits / near-miss top10 / scan config
REM  Env vars        : AUTO_OPEN=1  -> open the Excel automatically when done
REM                    NO_PAUSE=1   -> do not pause at the end (for scheduler)
REM ==========================================================================

REM ---- config: set AUTO_OPEN=1 to open the result workbook automatically ----
if not defined AUTO_OPEN set "AUTO_OPEN=0"

set "OUT_DIR=%CD%\output\scanDaily"
set "ARGS=%*"
if "%ARGS%"=="" set "ARGS=--universe all --fresh-days 5"

REM ---- pick interpreter: .venv > python > py ----
set "PY="
if exist "%CD%\.venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY where py >nul 2>nul && set "PY=py"
if not defined PY (
    echo [ERROR] Python not found. Install Python or add it to PATH,
    echo         or create .venv in this folder.
    if not defined NO_PAUSE pause
    endlocal & exit /b 1
)

echo ==========================================================================
echo   Route B scan  (template shape match + MACD golden-cross freshness)
echo --------------------------------------------------------------------------
echo   python : %PY%
echo   args   : %ARGS%
echo   start  : %DATE% %TIME%
echo ==========================================================================
echo.

"%PY%" scan_buy.py %ARGS%
set "RC=%ERRORLEVEL%"

echo.
echo --------------------------------------------------------------------------
if not "%RC%"=="0" (
    echo [FAILED] scan_buy.py exited with code %RC% - see log above.
    tasklist /fi "imagename eq EXCEL.EXE" 2>nul | find /i "EXCEL.EXE" >nul && (
        echo [HINT] Excel is running. If a buy_alert_*.xlsx is open there,
        echo        close it first: a locked workbook cannot be overwritten.
    )
    if not defined NO_PAUSE pause
    endlocal & exit /b %RC%
)
echo [DONE] %DATE% %TIME%

REM ---- locate the newest result file (skip when a custom --out was given) ----
set "HAS_OUT="
if not "%ARGS%"=="%ARGS:--out=%" set "HAS_OUT=1"
if defined HAS_OUT goto :report_custom

set "LATEST="
for /f "delims=" %%F in ('dir /b /a-d /o-d "%OUT_DIR%\buy_alert_*.xlsx" 2^>nul') do if not defined LATEST set "LATEST=%%F"
if not defined LATEST goto :no_file

echo [RESULT] %OUT_DIR%\%LATEST%
if not "%AUTO_OPEN%"=="1" goto :finish
echo [OPEN] opening the result file ...
start "" "%OUT_DIR%\%LATEST%"
goto :finish

:report_custom
echo [INFO] custom --out detected - see the saved path above.
goto :finish

:no_file
echo [INFO] no buy_alert_*.xlsx found in %OUT_DIR%

:finish
echo --------------------------------------------------------------------------
if not defined NO_PAUSE pause
endlocal & exit /b 0
