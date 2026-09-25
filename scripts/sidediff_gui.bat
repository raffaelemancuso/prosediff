@echo off
rem Open the sidediff window, from this checkout of the project.
rem
rem   sidediff_gui.bat                     the window, with the last choices
rem   sidediff_gui.bat REPOSITORY          the repository prefilled
rem   sidediff_gui.bat OLD.docx NEW.docx   two Markdown or Word files prefilled
rem
rem The project is the folder above this script. uvw is uv's launcher without a
rem console window; start returns at once, so no console stays open.

where uvw >nul 2>nul
if errorlevel 1 (
    echo sidediff_gui: uvw not found: install uv from https://docs.astral.sh/uv/
    pause
    exit /b 1
)

start "" uvw run --project "%~dp0.." sidediff-gui %*
