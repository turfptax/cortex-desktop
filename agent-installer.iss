; Inno Setup script for the Cortex Ingest Agent (the slim exe).
; NEW AppId on purpose: this is a new application, installed beside the
; legacy CortexHub, never an upgrade of it. The legacy app retires on its
; own schedule.

#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

[Setup]
AppId={{B7E6C9D4-3A51-4F2E-9C08-6D1FA2B45E77}
AppName=Cortex Ingest Agent
AppVersion={#MyAppVersion}
AppPublisher=TURFPTAx
DefaultDirName={autopf}\CortexIngest
DefaultGroupName=Cortex Ingest Agent
DisableProgramGroupPage=yes
OutputBaseFilename=CortexIngest-Setup-{#MyAppVersion}
Compression=lzma
SolidCompression=yes
PrivilegesRequired=lowest
CloseApplications=yes

[Tasks]
Name: "startup"; Description: "Start the Agent when Windows starts"; \
  GroupDescription: "Startup:"

[Files]
Source: "dist\CortexIngest\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Cortex Ingest Agent"; Filename: "{app}\CortexIngest.exe"
Name: "{group}\Uninstall Cortex Ingest Agent"; Filename: "{uninstallexe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "CortexIngest"; \
  ValueData: """{app}\CortexIngest.exe"""; Flags: uninsdeletevalue; \
  Tasks: startup

[Run]
Filename: "{app}\CortexIngest.exe"; Description: "Launch the Agent now"; \
  Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssInstall then
    Exec('taskkill', '/F /IM CortexIngest.exe', '', SW_HIDE,
         ewWaitUntilTerminated, ResultCode);
end;
