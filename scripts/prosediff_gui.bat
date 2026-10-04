@echo off
rem Open the prosediff window.
rem
rem   prosediff_gui.bat                     the window, with the last choices
rem   prosediff_gui.bat REPOSITORY          the repository prefilled
rem   prosediff_gui.bat FILE.docx           the file prefilled in the One file tab
rem   prosediff_gui.bat OLD.docx NEW.docx   two Markdown or Word files prefilled
rem
rem A batch file always runs in a console, however briefly. For no console at
rem all, start prosediff-gui.exe itself: install it once with
rem     uv tool install --editable C:\path\to\prosediff
rem and double-click it, pin it, make a shortcut to it, or drop files on it.
rem This script starts the project's own prosediff-gui.exe (in its .venv,
rem made by uv sync; the project is the folder above this script), which
rem runs the project's code as it is; else the one installed; else the window from the project
rem with uvw, uv's launcher without a console window.

if exist "%~dp0..\.venv\Scripts\prosediff-gui.exe" (
    start "" "%~dp0..\.venv\Scripts\prosediff-gui.exe" %*
    exit /b 0
)

where prosediff-gui >nul 2>nul
if not errorlevel 1 (
    start "" prosediff-gui %*
    exit /b 0
)

where uvw >nul 2>nul
if errorlevel 1 (
    echo prosediff_gui: neither prosediff-gui nor uvw found: install uv from https://docs.astral.sh/uv/
    pause
    exit /b 1
)

start "" uvw run --project "%~dp0.." prosediff-gui %*
