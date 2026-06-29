; Inno Setup script - Legacy Wheel Hub
; Non-commercial use only
;
; NOTE: Steering-wheel DRIVERS are NOT bundled (licensing). Users install them
; separately from:  https://github.com/Mysli0210/Legacy-Logitech-wheels-for-W11
; This installer ships only the application.
;
; BUILD: place this .iss together with LegacyWheelHub.exe, wheel.png, wheel.ico
; and README.txt in one folder, then Compile. The installer is written to the
; "Output" subfolder next to this script.

#define MyAppName "Legacy Wheel Hub"
#define MyAppVersion "1.0"
#define MyAppPublisher "Sadooo"
#define MyAppURL "https://github.com/Sadooo27/legacy-wheel-hub"
#define MyAppExeName "LegacyWheelHub.exe"
#define MySrc "."

[Setup]
AppId={{8F2A6B14-3C77-4E59-9D21-0B6E4A1F77C2}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\wheel.ico
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableProgramGroupPage=yes
;PrivilegesRequired=lowest
OutputDir=Output
OutputBaseFilename=LegacyWheelHub_Setup
SetupIconFile={#MySrc}\wheel.ico
SolidCompression=yes
WizardStyle=modern
; Make Add/Remove Programs + file metadata read "Legacy Wheel Hub"
VersionInfoVersion=1.0.0.0
VersionInfoProductName={#MyAppName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#MySrc}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#MySrc}\wheel.png";       DestDir: "{app}"; Flags: ignoreversion
Source: "{#MySrc}\wheel.ico";       DestDir: "{app}"; Flags: ignoreversion
Source: "{#MySrc}\README.txt";      DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\wheel.ico"
Name: "{autodesktop}\{#MyAppName}";  Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon; IconFilename: "{app}\wheel.ico"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
Filename: "{app}\README.txt"; Description: "View the README (driver setup link)"; Flags: postinstall shellexec skipifsilent

[Code]
function GetUninstallString(): String;
var
  sUnInstPath: String;
  sUnInstallString: String;
begin
  sUnInstPath := ExpandConstant('Software\Microsoft\Windows\CurrentVersion\Uninstall\{#emit SetupSetting("AppId")}_is1');
  sUnInstallString := '';
  if not RegQueryStringValue(HKLM, sUnInstPath, 'UninstallString', sUnInstallString) then
    RegQueryStringValue(HKCU, sUnInstPath, 'UninstallString', sUnInstallString);
  Result := sUnInstallString;
end;

function InitializeSetup(): Boolean;
var
  UninstPath: String;
  ResultCode: Integer;
begin
  Result := True;
  UninstPath := GetUninstallString();
  if UninstPath <> '' then
  begin
    if MsgBox('Legacy Wheel Hub is already installed. Would you like to UNINSTALL the existing version before continuing?', mbConfirmation, MB_YESNO) = IDYES then
    begin
      UninstPath := RemoveQuotes(UninstPath);
      Exec(UninstPath, '/SILENT /NORESTART /SUPPRESSMSGBOXES', '', SW_SHOW, ewWaitUntilTerminated, ResultCode);
      Result := False;
    end;
  end;
end;
