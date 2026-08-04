#define AppName "DLP"
#define AppPublisher "DLP"
#ifndef AppVersion
  #define AppVersion "0.1.0"
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

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}"; Flags: preservestringtype

[UninstallDelete]
Type: filesandordirs; Name: "{app}"
