' AI File Classifier — Silent launcher wrapper
' Runs pythonw launcher.py with no console window.
' The Desktop/Start Menu shortcut points here.
Dim fso, sh, dir
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
dir     = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir
sh.Run "pythonw """ & dir & "\launcher.py""", 0, False
Set fso = Nothing
Set sh  = Nothing
