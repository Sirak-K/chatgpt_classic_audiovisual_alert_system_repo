Option Explicit
Dim title, body, timeoutSeconds, shell

title = "ChatGPT svar klart"
body = "ChatGPTs svar är klart."
timeoutSeconds = 10

If WScript.Arguments.Count >= 1 Then title = CStr(WScript.Arguments.Item(0))
If WScript.Arguments.Count >= 2 Then body = CStr(WScript.Arguments.Item(1))
If WScript.Arguments.Count >= 3 Then
    On Error Resume Next
    timeoutSeconds = CInt(WScript.Arguments.Item(2))
    If Err.Number <> 0 Then timeoutSeconds = 10
    Err.Clear
    On Error GoTo 0
End If

Set shell = CreateObject("WScript.Shell")
shell.Popup body, timeoutSeconds, title, 64 + 4096
