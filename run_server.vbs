Set WshShell = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.Run "cmd /c cd /d """ & scriptDir & """ && .venv\Scripts\pythonw.exe -m uvicorn app:app --host 127.0.0.1 --port 8000", 0, False
