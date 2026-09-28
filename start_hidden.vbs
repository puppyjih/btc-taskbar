' 콘솔 창 없이 백그라운드로 실행.
' 자동실행하려면 이 파일의 바로가기를 shell:startup 에 복사.
' 같은 폴더에 BtcTaskbar.exe가 있으면 exe를, 없으면 pythonw + 스크립트로 실행.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
If fso.FileExists(dir & "\BtcTaskbar.exe") Then
    CreateObject("Wscript.Shell").Run """" & dir & "\BtcTaskbar.exe""", 0, False
Else
    CreateObject("Wscript.Shell").Run "pythonw.exe """ & _
        dir & "\btc_taskbar.py""", 0, False
End If
