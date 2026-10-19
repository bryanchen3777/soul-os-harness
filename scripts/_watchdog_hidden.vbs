' _watchdog_hidden.vbs
'
' Purpose: Task Scheduler entry point for SoulOS_Watchdog.
'   schtasks action = wscript.exe C:\Users\bbfcc\.local\bin\soul-os-harness\scripts\_watchdog_hidden.vbs
'
' Why this wrapper exists: PowerShell 5.1 has no reliable "run fully hidden" switch
'   when spawned by Task Scheduler on an interactive logon. A .vbs launched by
'   wscript.exe with window style 0 (hidden) is the only way to guarantee no
'   console flashes once every 5 minutes on Bry's desktop.
'
' IMPORTANT ARCHITECTURE RULE (docs/PROC-DEATH-FORENSICS-1.md §4):
'   The production server on :8000 may ONLY be started or restarted by a process
'   tree rooted at Task Scheduler, i.e. exactly this chain:
'       svchost -k netsvcs -s Schedule
'         -> wscript.exe  scripts\_watchdog_hidden.vbs   <-- this file
'           -> powershell.exe -File scripts\_watchdog.ps1
'             -> powershell.exe -File scripts\_start_plan_a.ps1
'               -> .venv\Scripts\python.exe run_server.py
'   Never invoke _watchdog.ps1 directly from a DSH session / subagent shell:
'   that would make the DSH harness an ancestor of the production server, which
'   the forensics ticket forbids. Only Task Scheduler may start it.
'
' Behavior:
'   * Resolves paths from this script's own location (no hardcoded repo path),
'     so a moved/renamed repo still works.
'   * Runs PowerShell SYNCHRONOUSLY (wait = True) so the scheduled task's
'     "Last Result" carries the watchdog's own exit code:
'       0 = healthy / tick skipped, 1 = fatal (e.g. git HEAD unreadable),
'       2 = CAP REACHED, auto-restart blocked (Bry intervention required),
'       3 = _watchdog.ps1 missing.
'     A single tick takes ~2-5s, so blocking does not delay the 5-minute cadence.
'   * NEVER pops a UI. Module-level "On Error Resume Next" guarantees that even an
'     unforeseen VBScript fault ends in a silent non-zero exit; diagnostics go to
'     data\logs\watchdog_wrapper.log. This is the whole point of the file: the
'     2026-10-08 outage was a modal "cannot find script" dialog every 5 minutes,
'     while the real damage (dead :8000) went unnoticed for ~23 hours.

Option Explicit

' Silent-fail guard: must stay module level so every statement below is covered.
On Error Resume Next

Const LOG_NAME = "watchdog_wrapper.log"

Dim shell, fso, scriptDir, repoRoot, ps1Path, logPath, logDir, cmd, exitCode
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
repoRoot = fso.GetParentFolderName(scriptDir)
ps1Path = fso.BuildPath(scriptDir, "_watchdog.ps1")
logDir = fso.BuildPath(repoRoot, "data\logs")
logPath = fso.BuildPath(logDir, LOG_NAME)

' Creates every missing level of a directory path. Scripting.FileSystemObject's
' CreateFolder is NOT recursive, so a single call would silently fail whenever
' more than one level is missing (verified by test, 2026-10-08).
Sub EnsureFolder(path)
    Dim parts, i, built
    On Error Resume Next
    parts = Split(path, "\")
    built = parts(0)
    For i = 1 To UBound(parts)
        built = built & "\" & parts(i)
        If Len(parts(i)) > 0 Then
            If Not fso.FileExists(built) Then
                If Not fso.FolderExists(built) Then fso.CreateFolder built
            End If
        End If
    Next
End Sub

' Writes one diagnostic line. Every argument is passed in because under
' Option Explicit an assigned name inside a Sub would be a new (undeclared)
' local, not the module-level variable. On Error Resume Next is repeated here so
' logging can never raise a secondary error.
Sub WriteWrapperLog(path, line)
    Dim fh
    On Error Resume Next
    EnsureFolder fso.GetParentFolderName(path)
    Set fh = fso.OpenTextFile(path, 8, True)
    fh.WriteLine line
    fh.Close
End Sub

If Not fso.FileExists(ps1Path) Then
    WriteWrapperLog logPath, "[" & Now & "] FATAL _watchdog.ps1 not found at: " & ps1Path
    WScript.Quit 3
End If

shell.CurrentDirectory = repoRoot

' Argument list mirrors the idiom _watchdog.ps1 already uses for its own child
' processes (_collect_crash_dumps.ps1 / _start_plan_a.ps1): no profile, bypass
' execution policy, hidden window.
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & ps1Path & """"

' 0 = hidden window, True = wait for completion (keeps exit code observable).
exitCode = shell.Run(cmd, 0, True)

If Err.Number <> 0 Then
    WriteWrapperLog logPath, "[" & Now & "] ERROR launching " & ps1Path & " (hr=" & Err.Number & " " & Err.Description & ")"
    WScript.Quit 4
End If

If exitCode <> 0 Then
    WriteWrapperLog logPath, "[" & Now & "] WARN _watchdog.ps1 exited with code " & exitCode
End If

WScript.Quit exitCode