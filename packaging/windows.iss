; Per-user installer: no administrator privileges are needed.
#define AppVersion GetEnv('CHEMSUITE_VERSION')
#define BuildRoot GetEnv('CHEMSUITE_BUILD_ROOT')
[Setup]
AppId={{8FCE0D8B-4377-483A-BD81-4E52D4AF38C6}
AppName=Chem Suite
AppVersion={#AppVersion}
AppPublisher=ChemSuite
AppPublisherURL=https://github.com/stpntrsvv/ChemSuite
DefaultDirName={localappdata}\Programs\Chem Suite
DefaultGroupName=Chem Suite
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.19041
OutputDir={#BuildRoot}\release
OutputBaseFilename=ChemSuite-{#AppVersion}-windows-x64-setup
SetupIconFile=..\src\chem_suite\desktop\assets\chemsuite.ico
UninstallDisplayIcon={app}\ChemSuite.exe
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; Flags: unchecked
[Files]
Source: "{#BuildRoot}\dist\ChemSuite\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\Chem Suite"; Filename: "{app}\ChemSuite.exe"
Name: "{autodesktop}\Chem Suite"; Filename: "{app}\ChemSuite.exe"; Tasks: desktopicon
[Run]
Filename: "{app}\ChemSuite.exe"; Description: "{cm:LaunchProgram,Chem Suite}"; Flags: nowait postinstall skipifsilent
