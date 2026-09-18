@echo off
chcp 65001 >nul 2>nul
title 小爱课程表 · HNUST 教务助手

echo ============================================
echo    小爱课程表 · HNUST 教务助手
echo ============================================
echo.

:: 直接启动原始打包程序
set "EXE=D:\短视频\XiaoAi湖南科技大学.exe"

if exist "%EXE%" (
    echo 正在启动...
    start "" "%EXE%"
    exit /b 0
)

echo [ERROR] 未找到 %EXE%
echo.
echo 请确认 XiaoAi湖南科技大学.exe 在 D:\短视频\ 目录下
pause
