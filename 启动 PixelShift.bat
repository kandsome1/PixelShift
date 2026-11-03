@echo off
rem ---------------------------------------------------------------
rem  PixelShift launcher
rem  Keep this file pure ASCII: cmd.exe parses .bat with the OEM code
rem  page, so non-ASCII text inside a batch file breaks line parsing.
rem ---------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"
title PixelShift

where python >nul 2>nul
if errorlevel 1 goto no_python

python -c "import PIL, tkinter" >nul 2>nul
if not errorlevel 1 goto launch

echo [setup] Pillow is missing, installing requirements...
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto no_pillow

:launch
where pythonw >nul 2>nul
if errorlevel 1 goto launch_console
start "" pythonw "%~dp0app.py"
exit /b 0

:launch_console
python "%~dp0app.py"
if errorlevel 1 pause
exit /b 0

:no_python
echo.
echo [ERROR] "python" was not found in PATH.
echo Install Python 3.9+ and tick "Add python.exe to PATH".
echo Download: https://www.python.org/downloads/
echo.
pause
exit /b 1

:no_pillow
echo.
echo [ERROR] Failed to install Pillow.
echo Run this manually, then start again:
echo     python -m pip install -r requirements.txt
echo.
pause
exit /b 1
