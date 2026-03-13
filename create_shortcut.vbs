' ============================================================
'  Crée un raccourci "Agent Platform" sur le bureau Windows
'  Appelé automatiquement par install.bat
'  Peut aussi être exécuté manuellement : cscript create_shortcut.vbs
' ============================================================

Dim oWS, oSC
Dim sRoot, sDesktop, sShortcut, sIcon

' Répertoire du script (= racine du projet)
sRoot = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' Bureau de l'utilisateur courant
Set oWS = WScript.CreateObject("WScript.Shell")
sDesktop = oWS.SpecialFolders("Desktop")

' ── Raccourci principal (Production) ──────────────────────────
sShortcut = sDesktop & "\Agent Platform.lnk"
Set oSC = oWS.CreateShortcut(sShortcut)

oSC.TargetPath       = sRoot & "\launch.bat"
oSC.WorkingDirectory = sRoot
oSC.Description      = "Python Agent Platform — Lancer l'application"
oSC.WindowStyle      = 1   ' Fenêtre normale

' Icône : utilise l'icône CMD si pas d'icône personnalisée
Dim sIconPath
sIconPath = sRoot & "\assets\icon.ico"
If CreateObject("Scripting.FileSystemObject").FileExists(sIconPath) Then
    oSC.IconLocation = sIconPath & ", 0"
Else
    oSC.IconLocation = "%SystemRoot%\System32\cmd.exe, 0"
End If

oSC.Save
Set oSC = Nothing

' ── Raccourci Dev (optionnel) ─────────────────────────────────
sShortcut = sDesktop & "\Agent Platform (Dev).lnk"
Set oSC = oWS.CreateShortcut(sShortcut)

oSC.TargetPath       = sRoot & "\launch_dev.bat"
oSC.WorkingDirectory = sRoot
oSC.Description      = "Python Agent Platform — Mode développement (hot reload)"
oSC.WindowStyle      = 1
oSC.IconLocation     = "%SystemRoot%\System32\cmd.exe, 0"
oSC.Save
Set oSC = Nothing

Set oWS = Nothing

WScript.Echo "Raccourcis créés sur le bureau."
