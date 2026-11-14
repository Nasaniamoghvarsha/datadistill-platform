@echo off
rem Builds the pitch video from pitch\metrics.json (edit the numbers there). Add --preview for a fast low-res check.
"%~dp0..\.venv\Scripts\python.exe" "%~dp0build.py" %*
