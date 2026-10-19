#ifndef MyAppVersion
  #define MyAppVersion "0.1.0"
#endif

#define MyAppName "misevent"
#define MyAppExeName "misevent.exe"

[Setup]
; Όλα τα σχετικά μονοπάτια από τη ρίζα του repo (το .iss ζει στο desktop/)
SourceDir=..
AppId={{5E2B7C41-9A3D-4F6B-8C21-7D4E0A1B3F92}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher=misevent
DefaultDirName={localappdata}\Programs\misevent
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=installer-output
OutputBaseFilename=misevent-Setup-{#MyAppVersion}
; Μέγιστη συμπίεση — το ζητούμενο είναι το μικρότερο δυνατό setup.
Compression=lzma2/ultra64
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes
AppMutex=misevent-desktop-5E2B7C41
SetupIconFile=webapp\static\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "dist\misevent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; Flags: nowait postinstall skipifsilent
