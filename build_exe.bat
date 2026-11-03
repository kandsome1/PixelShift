@echo off
rem ---------------------------------------------------------------
rem  Build PixelShift.exe  (keep this file pure ASCII)
rem
rem    build_exe.bat            -> onedir (recommended, fast start)
rem    build_exe.bat onefile    -> single file exe (easy to share)
rem
rem  Output:
rem    dist\PixelShift\PixelShift.exe        (onedir)
rem    dist_onefile\PixelShift.exe           (onefile)
rem ---------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"

python -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [setup] Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto no_pyinstaller
)

if /i "%~1"=="onefile" goto onefile
if /i "%~1"=="onedir"  goto onedir
if "%~1"==""           goto onedir
echo usage: build_exe.bat [onedir^|onefile]
exit /b 1

:onedir
echo Building onedir ...
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build PixelShift.spec
if errorlevel 1 goto build_failed
echo.
echo Done: dist\PixelShift\PixelShift.exe
echo Tip : put ffmpeg.exe / ffprobe.exe into dist\PixelShift\_internal\ffmpeg\
goto end

:onefile
echo Building onefile ...
python -m PyInstaller --noconfirm --clean --distpath dist_onefile --workpath build_onefile PixelShift.onefile.spec
if errorlevel 1 goto build_failed
echo.
echo Done: dist_onefile\PixelShift.exe
goto end

:build_failed
echo.
echo [ERROR] Build failed, see the log above.
pause
exit /b 1

:no_pyinstaller
echo.
echo [ERROR] PyInstaller could not be installed.
echo Run manually: python -m pip install pyinstaller
echo.
pause
exit /b 1

:end
exit /b 0
