#define AppName "DLP"
#define AppPublisher "DLP"
#ifndef AppVersion
  #define AppVersion "0.3.0"
#endif

[Setup]
AppId={{5AEF239B-0F67-4B6F-A8A5-3E7E0B36C9A3}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\DLP
DefaultGroupName=DLP
OutputDir=..\..\dist
OutputBaseFilename=dlp-windows-x64-setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
LicenseFile=..\..\THIRD_PARTY_NOTICES.md

[Files]
Source: "..\..\dist\dlp\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\DLP"; Filename: "{app}\dlp.exe"

[Code]
const
  DlpRegistryKey = 'Software\DLP';
  DlpBroadcastWindow = $FFFF;
  DlpSettingChangeMessage = $001A;

function NormalizePath(const Value: string): string;
begin
  Result := Lowercase(Trim(Value));
  while (Length(Result) > 3) and (Result[Length(Result)] = '\') do
    Delete(Result, Length(Result), 1);
end;

function IsPathEntry(const Entry, Target: string): Boolean;
begin
  Result := CompareText(NormalizePath(Entry), NormalizePath(Target)) = 0;
end;

procedure KeepPathEntry(var Output: string; const Entry, Target: string);
begin
  if (Entry <> '') and not IsPathEntry(Entry, Target) then begin
    if Output <> '' then
      Output := Output + ';';
    Output := Output + Entry;
  end;
end;

function RemovePathEntry(const Value, Target: string): string;
var
  I, Start: Integer;
  Entry: string;
begin
  Result := '';
  Start := 1;
  I := 1;
  while I <= Length(Value) + 1 do begin
    if I > Length(Value) then begin
      Entry := Trim(Copy(Value, Start, I - Start));
      KeepPathEntry(Result, Entry, Target);
      Start := I + 1;
    end else if Value[I] = ';' then begin
      Entry := Trim(Copy(Value, Start, I - Start));
      KeepPathEntry(Result, Entry, Target);
      Start := I + 1;
    end;
    I := I + 1;
  end;
end;

function AddPathEntry(const Value, Target: string): string;
begin
  Result := RemovePathEntry(Value, Target);
  if Result <> '' then
    Result := Result + ';';
  Result := Result + Target;
end;

procedure BroadcastEnvironmentChange;
begin
  SendMessage(DlpBroadcastWindow, DlpSettingChangeMessage, 0, 0);
end;

procedure UpdateUserPath(const AddEntry: Boolean);
var
  ExistingPath, TargetPath, PreviousPath: string;
begin
  TargetPath := ExpandConstant('{app}');
  RegQueryStringValue(HKCU, DlpRegistryKey, 'InstallPath', PreviousPath);
  RegQueryStringValue(HKCU, 'Environment', 'Path', ExistingPath);

  if AddEntry then begin
    if PreviousPath <> '' then
      ExistingPath := RemovePathEntry(ExistingPath, PreviousPath);
    ExistingPath := AddPathEntry(ExistingPath, TargetPath);
    RegWriteExpandStringValue(HKCU, 'Environment', 'Path', ExistingPath);
    RegWriteStringValue(HKCU, DlpRegistryKey, 'InstallPath', TargetPath);
  end else begin
    if PreviousPath = '' then
      PreviousPath := TargetPath;
    ExistingPath := RemovePathEntry(ExistingPath, PreviousPath);
    if ExistingPath = '' then
      RegDeleteValue(HKCU, 'Environment', 'Path')
    else
      RegWriteExpandStringValue(HKCU, 'Environment', 'Path', ExistingPath);
    RegDeleteValue(HKCU, DlpRegistryKey, 'InstallPath');
  end;
  BroadcastEnvironmentChange;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    UpdateUserPath(True);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    UpdateUserPath(False);
end;

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
