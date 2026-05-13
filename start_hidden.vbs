Set WshShell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.Run "cmd /c cd /d """ & scriptDir & """ && .venv\Scripts\python.exe tray.py", 0, False
