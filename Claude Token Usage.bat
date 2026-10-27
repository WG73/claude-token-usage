@echo off
rem Startet die Anzeige ohne Konsolenfenster
where pythonw.exe >/dev/null 2>&1 && (start "" pythonw.exe "%~dp0claude_usage.pyw" & exit /b)
start "" pyw.exe "%~dp0claude_usage.pyw"
