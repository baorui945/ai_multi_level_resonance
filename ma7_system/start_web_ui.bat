@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

rem 用法：双击 start_web_ui.bat，或 start_web_ui.bat 8788 指定端口
set "PORT=8770"
if not "%~1"=="" set "PORT=%~1"
set "WEBAPP=%~dp0webapp"

title MA7 上抛 Web UI - 端口 %PORT%
echo ============================================================
echo   MA7 上抛扫描 Web UI
echo   地址 : http://127.0.0.1:%PORT%/
echo   目录 : %WEBAPP%
echo   停止 : 直接关掉本窗口（或按 Ctrl+C）
echo ============================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo [X] 没找到 python，请先安装 Python 并加入 PATH
  pause
  exit /b 1
)

set "LISTEN=0"
for /f "delims=" %%A in ('netstat -ano ^| findstr /c:":%PORT%" ^| findstr /c:"LISTENING"') do set "LISTEN=1"
if "%LISTEN%"=="1" (
  echo [i] 端口 %PORT% 已有服务，直接打开页面...
  start "" "http://127.0.0.1:%PORT%/"
  echo.
  echo     如需重启：先关掉占用 %PORT% 的那个窗口再运行本文件。
  ping -n 4 127.0.0.1 >nul
  exit /b 0
)

echo [i] 正在加载行情并启动服务，端口就绪后会自动打开浏览器...
echo     （首次启动约 3~4 秒；之后读取缓存，秒开）
echo.

set "MA7_WEB_PORT=%PORT%"
set "MA7_OPEN_BROWSER=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%WEBAPP%"
python server.py

echo.
echo 服务已退出。按任意键关闭窗口...
pause >nul
endlocal
