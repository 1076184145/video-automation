#define MyAppName "Video Automation"
#define MyAppVersion GetEnv("VIDEO_AUTOMATION_INSTALLER_VERSION")
#if MyAppVersion == ""
  #define MyAppVersion "0.1.0"
#endif
#define MyAppPublisher "Video Automation"
#define MyAppExeName "VideoAutomationLite.exe"
#define MySourceDir "..\dist\VideoAutomationLite"

[Setup]
AppId={{B342CFB7-DC9D-4E3C-9D40-5B4E13DB1197}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Video Automation
DefaultGroupName=Video Automation
DisableProgramGroupPage=yes
OutputDir=..\dist\installers
OutputBaseFilename=VideoAutomationLite-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
UninstallDisplayIcon={app}\{#MyAppExeName}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: ".env,.env.*,input\*,processing\*,logs\*,logs-runtime\*,*.log,desktop_app_error.log"

[Icons]
Name: "{group}\Video Automation"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\Video Automation"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Video Automation"; Flags: nowait postinstall skipifsilent
