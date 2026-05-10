; ============================================================
;  AI File Classifier — Inno Setup installer script
;  Requires Inno Setup 6.x  https://jrsoftware.org/isinfo.php
;
;  To build the installer:
;    1. Install Inno Setup 6 (free)
;    2. Open this file in the Inno Setup IDE, or run:
;         iscc installer\app.iss
;    3. Upload  installer\dist\AIFileClassifier-Setup.exe
;       to a GitHub Release.
; ============================================================

#define AppName      "AI File Classifier"
#define AppVersion   "1.0"
#define AppPublisher "KayodeAjayi200"
#define AppURL       "https://github.com/KayodeAjayi200/ai-file-classifier"
#define AppExeName   "run.vbs"

[Setup]
AppId={{D3A7F2C1-8B4E-4F9A-A2D1-5E6C7F8A9B0C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases

; Install to user's AppData — no administrator rights needed
DefaultDirName={localappdata}\{#AppName}
DisableDirPage=no
DisableProgramGroupPage=yes

; Output
OutputDir=dist
OutputBaseFilename=AIFileClassifier-Setup
SetupIconFile=..\app_icon.ico
UninstallDisplayIcon={app}\app_icon.ico

; Compression
Compression=lzma2/max
SolidCompression=yes

; Appearance
WizardStyle=modern
WizardSizePercent=120

; Privileges
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startmenu";  Description: "Add to &Start Menu";          GroupDescription: "Additional icons:"

[Files]
; Core app files
Source: "..\search.py";       DestDir: "{app}"; Flags: ignoreversion
Source: "..\launcher.py";     DestDir: "{app}"; Flags: ignoreversion
Source: "..\run.vbs";         DestDir: "{app}"; Flags: ignoreversion
Source: "..\config.py";       DestDir: "{app}"; Flags: ignoreversion
Source: "..\bump_version.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt";DestDir: "{app}"; Flags: ignoreversion
Source: "..\setup.bat";       DestDir: "{app}"; Flags: ignoreversion
Source: "..\app_icon.ico";    DestDir: "{app}"; Flags: ignoreversion

; README
Source: "..\README.md";       DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
; Start Menu
Name: "{autoprograms}\{#AppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\run.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"; Tasks: startmenu

; Desktop
Name: "{autodesktop}\{#AppName}"; Filename: "{sys}\wscript.exe"; Parameters: """{app}\run.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\app_icon.ico"; Tasks: desktopicon

[Run]
; Install Python packages after files are copied
Filename: "{cmd}"; Parameters: "/c python -m pip install -r ""{app}\requirements.txt"" --quiet"; StatusMsg: "Installing Python packages (Flask, Pillow, pystray…)"; Flags: waituntilterminated runhidden

; Offer to launch now
Filename: "{sys}\wscript.exe"; Parameters: """{app}\run.vbs"""; Description: "Launch {#AppName} now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Nothing extra needed — files are removed by Inno's uninstaller

[Code]
{ ── Pre-requisite check: Python 3.9+ ───────────────────────────────────────── }
function IsPythonOk(): Boolean;
var
  ResultCode: Integer;
begin
  { Try 'python --version'; exit code 0 means Python is in PATH }
  Result := Exec(ExpandConstant('{cmd}'),
                 '/c python --version >nul 2>&1',
                 '', SW_HIDE, ewWaitUntilTerminated, ResultCode)
            and (ResultCode = 0);
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  if not IsPythonOk() then begin
    if MsgBox(
      'Python 3.9 or later was not found on this computer.' + #13#10 + #13#10 +
      'Please install Python from https://python.org first.' + #13#10 +
      '(Make sure to tick "Add Python to PATH" during install.)' + #13#10 + #13#10 +
      'Continue anyway?',
      mbConfirmation, MB_YESNO) = IDNO then begin
      Result := False;
    end;
  end;
end;
