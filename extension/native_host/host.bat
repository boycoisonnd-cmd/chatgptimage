@echo off
rem Native Messaging host launcher for GPT Image Studio.
rem Chrome spawns this file directly (host manifest "path"). It forwards to
rem the real host implemented in Python (stdlib only) so we get clean framing.
rem Use the repo's venv python so "python" need not be on PATH.
set "PY=%~dp0..\..\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0host.py" %*
