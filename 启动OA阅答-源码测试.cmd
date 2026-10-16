@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "OA_URL=http://127.0.0.1:8787/"
set "OA_HEALTH_URL=http://127.0.0.1:8787/api/health"
set "OA_LOG=%~dp0output\launcher.log"

if not exist "%~dp0output" mkdir "%~dp0output"

rem Reuse an already-running OA阅答 service instead of starting a duplicate.
powershell.exe -NoProfile -Command "try { $h=Invoke-RestMethod '%OA_HEALTH_URL%' -TimeoutSec 1; if ($h.status -eq 'ready') { exit 0 } } catch {}; exit 1" >nul 2>nul
if not errorlevel 1 goto already_running

set "OA_PYTHON="
py -c "import sys" >nul 2>nul
if not errorlevel 1 set "OA_PYTHON=py"

if defined OA_PYTHON goto python_ready
if not exist "C:\Program Files\LibreOffice\program\python.exe" goto python_missing
set "OA_PYTHON="C:\Program Files\LibreOffice\program\python.exe""

:python_ready
if defined OA_LAUNCH_NO_BROWSER goto browser_waiter_done
start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command "$deadline=(Get-Date).AddSeconds(30); do { try { $h=Invoke-RestMethod '%OA_HEALTH_URL%' -TimeoutSec 1; if ($h.status -eq 'ready') { Start-Process '%OA_URL%'; exit 0 } } catch {}; Start-Sleep -Milliseconds 250 } while ((Get-Date) -lt $deadline)"

:browser_waiter_done
echo 正在启动 OA阅答 v1.0.1，请稍候……
echo 启动日志：%OA_LOG%
%OA_PYTHON% bridge\bridge.py > "%OA_LOG%" 2>&1
set "OA_EXIT=%ERRORLEVEL%"
if "%OA_EXIT%"=="0" exit /b 0

echo.
echo OA阅答启动失败，错误详情如下：
type "%OA_LOG%"
echo.
echo 请保留此窗口或 launcher.log，以便排查。
pause
exit /b %OA_EXIT%

:already_running
echo OA阅答已经在本机运行，正在打开……
if not defined OA_LAUNCH_NO_BROWSER start "" "%OA_URL%"
exit /b 0

:python_missing
echo 未找到可用的 Python，无法启动源码测试版。
echo 请使用已构建的 OA阅答 安装版，或安装 Python 3.12 及以上版本后重试。
pause
exit /b 2
