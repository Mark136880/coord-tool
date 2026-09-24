; Inno Setup 脚本:生成"坐标距离计算"安装包
; 安装到当前用户目录(%LOCALAPPDATA%\CoordTool),无需管理员权限。
; 文件按 dist 的相对结构原样部署,保证 exe 能定位到同目录的 tesseract/terrain-packs/config。
#define MyAppName "坐标距离计算"
#define MyAppVersion "1.0.0"
#define MyAppExeName "坐标距离计算.exe"
#define MyAppPublisher "CoordTool"
#define MyAppAssocExt ""

[Setup]
AppId={{8F2C9E4A-6B45-4D1A-9C3E-5A6B7C8D9E0F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\CoordTool
OutputDir=..\installer\out
OutputBaseFilename=坐标距离计算-安装包
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 安装包图标与程序一致
SetupIconFile=..\logo.ico
; 用户级安装,免 UAC
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; 每次运行无需重新编译缓存
DisableProgramGroupPage=yes
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 全部按 dist 相对结构部署
Source: "..\dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 图标一并部署,供快捷方式引用
Source: "..\logo.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo.ico"
Name: "{userdesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\logo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 配置也随卸载清掉(可选;如需保留用户配置请删除下一行)
Type: filesandordirs; Name: "{app}"