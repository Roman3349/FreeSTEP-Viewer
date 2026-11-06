; Inno Setup script of the FreeSTEP Viewer installer.
;
; Build after PyInstaller (from the repository root):
;   iscc /DAppVersion=0.2.1 packaging\windows\installer.iss
; Output: dist\FreeStepViewer-<version>-setup.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "FreeSTEP Viewer"
#define AppExe "FreeStepViewer.exe"
#define Root "..\.."

[Setup]
AppId={{13655979-C10D-473D-A766-211D7DC5C2C7}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Roman Ondráček
AppPublisherURL=https://github.com/Roman3349/FreeSTEP-Viewer
AppSupportURL=https://github.com/Roman3349/FreeSTEP-Viewer/issues
AppUpdatesURL=https://github.com/Roman3349/FreeSTEP-Viewer/releases
LicenseFile={#Root}\LICENSE
DefaultDirName={autopf}\FreeSTEP Viewer
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Install for the current user without administrator rights, or for all users
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#Root}\dist
OutputBaseFilename=FreeStepViewer-{#AppVersion}-setup
SetupIconFile={#Root}\build\icons\freestep-viewer.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "czech"; MessagesFile: "compiler:Languages\Czech.isl"

[CustomMessages]
english.AssociateFre=Open FreeSTEP exam files (.fre) with {#AppName}
czech.AssociateFre=Otevírat soubory vyšetření FreeSTEP (.fre) v aplikaci {#AppName}
english.AddToPath=Add the freestep command line tool to PATH
czech.AddToPath=Přidat nástroj příkazové řádky freestep do proměnné PATH

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "associatefre"; Description: "{cm:AssociateFre}"; Flags: unchecked
Name: "addtopath"; Description: "{cm:AddToPath}"; Flags: unchecked

[Files]
Source: "{#Root}\dist\FreeStepViewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.fre\OpenWithProgids"; ValueType: string; ValueName: "FreeStepViewer.fre"; ValueData: ""; Flags: uninsdeletevalue; Tasks: associatefre
Root: HKA; Subkey: "Software\Classes\FreeStepViewer.fre"; ValueType: string; ValueName: ""; ValueData: "FreeSTEP exam"; Flags: uninsdeletekey; Tasks: associatefre
Root: HKA; Subkey: "Software\Classes\FreeStepViewer.fre\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"; Tasks: associatefre
Root: HKA; Subkey: "Software\Classes\FreeStepViewer.fre\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""; Tasks: associatefre
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".zip"; ValueData: ""; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Applications\{#AppExe}\SupportedTypes"; ValueType: string; ValueName: ".fre"; ValueData: ""

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  EnvironmentKey = 'Environment';

function PathKeyRoot: Integer;
begin
  if IsAdminInstallMode then
    Result := HKEY_LOCAL_MACHINE
  else
    Result := HKEY_CURRENT_USER;
end;

function PathSubKey: String;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := EnvironmentKey;
end;

{ Add or remove the installation directory in PATH (user or system, matching the install mode). }
procedure UpdatePath(Add: Boolean);
var
  Paths, Dir: String;
  Position: Integer;
begin
  if not RegQueryStringValue(PathKeyRoot, PathSubKey, 'Path', Paths) then
    Paths := '';
  Dir := ExpandConstant('{app}');
  Position := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';');
  if Add and (Position = 0) then
  begin
    if (Paths <> '') and (Paths[Length(Paths)] <> ';') then
      Paths := Paths + ';';
    RegWriteExpandStringValue(PathKeyRoot, PathSubKey, 'Path', Paths + Dir);
  end
  else if (not Add) and (Position > 0) then
  begin
    Delete(Paths, Position, Length(Dir) + 1);
    RegWriteExpandStringValue(PathKeyRoot, PathSubKey, 'Path', Paths);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    UpdatePath(True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    UpdatePath(False);
end;
