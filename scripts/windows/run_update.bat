@echo off
setlocal
rem NHIT | Task Scheduler entry point: crawl IHMCL -> rebuild JSON -> push.
rem Registered by register_update_task.ps1; safe to run by hand too.
cd /d "%~dp0..\.."
if not exist "logs" mkdir "logs"
echo [%date% %time%] scheduled run start >> "logs\scheduled_runs.log"
".venv\Scripts\python.exe" "scripts\update_and_publish.py" >> "logs\scheduled_runs.log" 2>&1
set RC=%ERRORLEVEL%
echo [%date% %time%] scheduled run exit %RC% >> "logs\scheduled_runs.log"
exit /b %RC%
