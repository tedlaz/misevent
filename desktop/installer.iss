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

[Code]
// Τα δεδομένα (%LOCALAPPDATA%\misevent, βλ. launcher.py) ζουν έξω από το {app},
// οπότε η απεγκατάσταση τα αφήνει. Ερώτηση με προεπιλογή «No»· σε silent
// απεγκατάσταση δεν σβήνεται τίποτα. Τα κείμενα στα αγγλικά, όπως όλος ο
// installer (Default.isl, αρχείο χωρίς BOM).
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep <> usPostUninstall then
    exit;
  DataDir := ExpandConstant('{localappdata}\misevent');
  if not DirExists(DataDir) then
    exit;
  if SuppressibleMsgBox('Also delete all misevent data (database, users, companies, backups)?'
      + #13#10#13#10 + DataDir + #13#10#13#10
      + 'This cannot be undone. Choose No to keep the data for a future reinstall.',
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
    if not DelTree(DataDir, True, True, True) then
      MsgBox('Could not delete ' + DataDir + '. Delete it manually.', mbError, MB_OK);
end;
