@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "PORT=8770"
if not "%~1"=="" set "PORT=%~1"
set "WEBAPP=%~dp0webapp"

title MA7 上抛 Web UI 启动器
echo ============================================================
echo   MA7 上抛扫描 Web UI
echo   端口 : %PORT%
echo   目录 : %WEBAPP%
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [X] 没找到 python，请先安装 Python 并加入 PATH
  pause
  exit /b 1
)

call :IsListening
if "%LISTEN%"=="1" (
  echo [i] 端口 %PORT% 已有服务在跑，直接打开页面...
  echo [TEST] would open http://127.0.0.1:%PORT%/
  echo.
  echo     如果页面打不开，先关掉占用 %PORT% 的那个黑窗口再重试。
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)

echo [i] 正在启动服务（新窗口：MA7 Web Server）...
start "MA7 Web Server" /D "%WEBAPP%" cmd /k "chcp 65001 >nul & set PYTHONUTF8=1 & set PYTHONIOENCODING=utf-8 & set MA7_WEB_PORT=%PORT% & python server.py"

set /a WAIT=0
:waitloop
ping -n 2 127.0.0.1 >nul
set /a WAIT+=1
call :IsListening
if "%LISTEN%"=="1" goto ready
if %WAIT% lss 40 goto waitloop
echo.
echo [X] 40 秒内没起来，看看 "MA7 Web Server" 窗口里的报错
pause
exit /b 1

:ready
echo [OK] 服务已就绪（用了 %WAIT% 秒），正在打开浏览器...
echo [TEST] would open http://127.0.0.1:%PORT%/
echo.
echo     关闭服务：关掉 "MA7 Web Server" 那个黑窗口
echo     本窗口 3 秒后自动关闭
ping -n 4 127.0.0.1 >nul
exit /b 0

:IsListening
set "LISTEN=0"
for /f "delims=" %%A in ('netstat -ano ^| findstr /c:":%PORT%" ^| findstr /c:"LISTENING"') do set "LISTEN=1"
exit /b 0
