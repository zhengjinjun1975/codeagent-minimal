@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo   CodeAgent 审查台（本地，数据不出厂）
echo   用途：给代码做整体体检，出一份"能不能放行"的审查报告
echo ============================================================
echo.

rem ── 1) 找一个能用的 Python（3.8+）──────────────────────────
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo [错误] 没找到 Python。请先安装 Python 3.8 或更高版本：
  echo        https://www.python.org/downloads/  （安装时勾选 "Add python.exe to PATH"）
  echo.
  pause
  exit /b 1
)
%PY% -c "import sys; raise SystemExit(0 if sys.version_info>=(3,8) else 1)" 2>nul
if errorlevel 1 (
  echo [错误] Python 版本过低，需要 3.8 或更高。
  pause
  exit /b 1
)

rem ── 2) 受审目录：把文件夹拖到本文件上即可指定，不拖默认审查本仓库 ──
set "PORT=8087"
set "TARGETARG="
if not "%~1"=="" (
  set "TARGETARG=--target-root "%~1""
  echo 受审目录: %~1
) else (
  echo 受审目录: 本仓库自身（想审别的项目，把那个文件夹拖到本文件上再松开）
)
echo 访问地址: http://127.0.0.1:%PORT%
echo 报告导出: 浏览器里生成报告后，可下载 HTML / Markdown / SARIF
echo.

rem ── 3) 开浏览器 + 起服务（关掉本窗口即停止服务）────────────
rem   设 CODEREVIEW_NO_BROWSER=1 可跳过自动开浏览器（自测/无桌面环境用）
if not "%CODEREVIEW_NO_BROWSER%"=="1" start "" "http://127.0.0.1:%PORT%"
%PY% "lab\lab_app.py" --port %PORT% %TARGETARG%
echo.
echo 服务已停止。
pause
