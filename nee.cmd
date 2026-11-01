@echo off
setlocal
python "%~dp0nee.py" %*
exit /b %ERRORLEVEL%

