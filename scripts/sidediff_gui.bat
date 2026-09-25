@echo off
rem Open the sidediff window.
rem
rem   sidediff_gui.bat                     the window, with the last choices
rem   sidediff_gui.bat REPOSITORY          the repository prefilled
rem   sidediff_gui.bat FILE.docx           a dialog asks for the file to compare it with
rem   sidediff_gui.bat OLD.docx NEW.docx   two Markdown or Word files prefilled
rem
rem A batch file always runs in a console, however briefly. For no console at
rem all, start sidediff-gui.exe itself: install it once with
rem     uv tool install --editable C:\path\to\sidediff
rem and double-click it, pin it, make a shortcut to it, or drop files on it.
rem This script uses it when it is installed, and otherwise runs the window
rem from the project (the folder above this script) with uvw, uv's launcher
rem without a console window.

where sidediff-gui >nul 2>nul
if not errorlevel 1 (
    start "" sidediff-gui %*
    exit /b 0
)

where uvw >nul 2>nul
if errorlevel 1 (
    echo sidediff_gui: neither sidediff-gui nor uvw found: install uv from https://docs.astral.sh/uv/
    pause
    exit /b 1
)

start "" uvw run --project "%~dp0.." sidediff-gui %*
