#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
[Setup]
AppId={{1309508C-9DCC-47DF-9368-7846DE12A849}
AppName=Serial Desk
AppVersion={#AppVersion}
AppPublisher=MusicCityTelecom
AppPublisherURL=https://github.com/MusicCityTelecom/linux-console-gui
AppSupportURL=https://github.com/MusicCityTelecom/linux-console-gui/issues
AppUpdatesURL=https://github.com/MusicCityTelecom/linux-console-gui/releases
DefaultDirName={localappdata}\Programs\Serial Desk
DefaultGroupName=Serial Desk
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist-windows
OutputBaseFilename=SerialDesk-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\LICENSE
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\SerialDesk.exe
[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: unchecked
[Files]
Source: "..\build-windows\dist\SerialDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Serial Desk"; Filename: "{app}\SerialDesk.exe"
Name: "{group}\Uninstall Serial Desk"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Serial Desk"; Filename: "{app}\SerialDesk.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\SerialDesk.exe"; Description: "Open Serial Desk"; Flags: nowait postinstall skipifsilent
