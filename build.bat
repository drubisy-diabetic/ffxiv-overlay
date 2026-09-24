@echo off
rem =============================================================================
rem  Build the FFXIV Overlay single-file Windows executable with PyInstaller.
rem
rem  Requirements (once):
rem      pip install PyQt6 websockets pyinstaller
rem
rem  Output:  dist\FFXIV_Overlay.exe   (portable, one-file, no console window)
rem =============================================================================
setlocal

if not exist overlay.py (
    echo Error: run this from the ffxiv-overlay project root.
    exit /b 1
)

echo Building FFXIV_Overlay.exe (one-file, Qt6 platform plugins + icons bundled)...

python -m PyInstaller ^
    --noconsole ^
    --onefile ^
    --collect-all PyQt6 ^
    --add-data "icons;icons" ^
    --name "FFXIV_Overlay" ^
    overlay.py

if errorlevel 1 (
    echo.
    echo Build FAILED.
    exit /b 1
)

echo.
echo Build succeeded -> dist\FFXIV_Overlay.exe
endlocal
