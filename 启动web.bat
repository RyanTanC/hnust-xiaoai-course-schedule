@echo off
chcp 65001 >nul 2>nul
title 小爱课程表 · Web 版
cd /d "%~dp0"

echo ============================================
echo    小爱课程表 · Web 版
echo ============================================
echo.

set "PY=C:\Users\Hyper_hui\AppData\Local\Python\pythoncore-3.14-64\python.exe"

if not exist "%PY%" (
    echo [ERROR] 未找到 Python
    pause
    exit /b 1
)

echo [OK] Python: %PY%
echo [1/2] 启动服务...

if not exist "data" mkdir data

echo.
echo --------------------------------------------
echo    http://127.0.0.1:8080
echo    按 Ctrl+C 停止
echo --------------------------------------------
echo.

start "" "http://127.0.0.1:8080"
"%PY%" app.py
echo.
pause
