@echo off
chcp 65001 >nul 2>nul
title 小爱课程表 · HNUST 教务助手

:: 切换到脚本所在目录
cd /d "%~dp0"
if %errorlevel% neq 0 (
    echo [ERROR] 无法切换到脚本目录
    echo 路径可能包含特殊字符，请将项目移到简单路径下，如 D:\app\
    pause
    exit /b 1
)

echo ============================================
echo    小爱课程表 · HNUST 教务助手
echo ============================================
echo.
echo 当前目录: %cd%
echo.

:: ---- 查找可用 Python ----
set "PYTHON_CMD="

where python >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python"
    goto :found
)

where python3 >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=python3"
    goto :found
)

where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PYTHON_CMD=py"
    goto :found
)

echo [ERROR] 未找到 Python!
echo.
echo 请执行以下步骤:
echo   1. 安装 Python 3.10+: https://www.python.org/downloads/
echo   2. 安装时勾选 "Add Python to PATH"
echo   3. 重新运行本脚本
echo.
pause
exit /b 1

:found
echo [OK] Python: %PYTHON_CMD%
"%PYTHON_CMD%" --version
echo.

:: ---- 检查并安装依赖 ----
echo [1/3] 检查依赖...
"%PYTHON_CMD%" -c "import flask" >nul 2>nul
if %errorlevel% neq 0 (
    echo      Flask 未安装，正在安装...
    "%PYTHON_CMD%" -m pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo [ERROR] 依赖安装失败!
        echo 请手动运行: %PYTHON_CMD% -m pip install -r requirements.txt
        pause
        exit /b 1
    )
    echo      依赖安装完成
) else (
    echo      依赖已就绪
)

:: ---- 检查 Playwright ----
echo [2/3] 检查 Playwright...
"%PYTHON_CMD%" -c "from playwright.sync_api import sync_playwright" >nul 2>nul
if %errorlevel% neq 0 (
    echo      Playwright 未安装，正在安装...
    "%PYTHON_CMD%" -m pip install playwright
    "%PYTHON_CMD%" -m playwright install chromium
) else (
    echo      Playwright 已就绪
)

:: ---- 启动 ----
echo [3/3] 启动服务...
echo.
echo ============================================
echo    http://127.0.0.1:8080
echo    按 Ctrl+C 停止
echo ============================================
echo.

start "" "http://127.0.0.1:8080" >nul 2>nul

"%PYTHON_CMD%" app.py
echo.
echo 服务已停止
pause
