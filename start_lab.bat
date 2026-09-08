@echo off
chcp 65001 >nul
rem CodeAgent Lab - local code agent one-click start
cd /d "%~dp0"
echo ============================================================
echo  CodeAgent Lab - local full code agent
echo  http://127.0.0.1:8087    (Ctrl+C to exit)
echo  Data stays local, no outbound traffic
echo ============================================================
python lab\lab_app.py --port 8087
pause
