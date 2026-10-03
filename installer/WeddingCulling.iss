#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
[Setup]
AppId={{203A25AC-65D0-4FA9-B699-93E77BFE5334}
AppName=WeddingCulling
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\WeddingCulling
DefaultGroupName=WeddingCulling
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist\installer
OutputBaseFilename=WeddingCulling-Setup-{#AppVersion}-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\WeddingCulling.exe
CloseApplications=yes
RestartApplications=no
[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
[Tasks]
Name: "desktopicon"; Description: "Criar atalho na área de trabalho"; GroupDescription: "Atalhos adicionais:"; Flags: unchecked
[Files]
Source: "..\dist\WeddingCulling\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\WeddingCulling"; Filename: "{app}\WeddingCulling.exe"; WorkingDir: "{app}"
Name: "{userdesktop}\WeddingCulling"; Filename: "{app}\WeddingCulling.exe"; WorkingDir: "{app}"; Tasks: desktopicon
[Run]
Filename: "{app}\WeddingCulling.exe"; Description: "Abrir WeddingCulling"; Flags: nowait postinstall skipifsilent
