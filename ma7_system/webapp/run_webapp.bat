@echo off
chcp 65001 >nul
rem 兼容旧入口：转发到项目根目录的 start_web_ui.bat
call "%~dp0..\start_web_ui.bat" %*
