; One-click Windows installer. Built by CI:  iscc installer\winmeh.iss
; Installs per-user (no admin prompt), bundles the app + chat model + AI engine + voice model.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6E1B7C2A-5D0F-4C64-9A44-57A1D3B0E9F1}
AppName=WinMeh
AppVersion={#AppVersion}
AppPublisher=WinMeh contributors
AppPublisherURL=https://github.com/0xAshraFF/WinMeh
DefaultDirName={autopf}\WinMeh
DefaultGroupName=WinMeh
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=WinMeh-Setup
SetupIconFile=winmeh.ico
UninstallDisplayIcon={app}\WinMeh.exe
Compression=lzma2/fast
SolidCompression=no
WizardStyle=modern
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Tasks]
Name: "startup"; Description: "Start WinMeh when I sign in"; GroupDescription: "Options:"
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Options:"; Flags: unchecked

[Files]
Source: "..\dist\WinMeh\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\WinMeh"; Filename: "{app}\WinMeh.exe"
Name: "{group}\Uninstall WinMeh"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WinMeh"; Filename: "{app}\WinMeh.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "WinMeh"; \
  ValueData: """{app}\WinMeh.exe"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\WinMeh.exe"; Description: "Start WinMeh now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /f /im WinMeh.exe & taskkill /f /im llama-server.exe"; Flags: runhidden; RunOnceId: "StopWinMeh"
