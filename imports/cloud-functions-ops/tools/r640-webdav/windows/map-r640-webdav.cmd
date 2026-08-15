@echo off
setlocal

set "DRIVE=Z:"
set "WEBDAV=\\files.re8ch.com@SSL@18444\DavWWWRoot"

net session >nul 2>&1
if errorlevel 1 (
  echo Please right-click this file and choose "Run as administrator".
  pause
  exit /b 1
)

rem Permit HTTPS Basic authentication and raise the Windows WebDAV file limit.
reg add "HKLM\SYSTEM\CurrentControlSet\Services\WebClient\Parameters" /v BasicAuthLevel /t REG_DWORD /d 2 /f >nul
reg add "HKLM\SYSTEM\CurrentControlSet\Services\WebClient\Parameters" /v FileSizeLimitInBytes /t REG_DWORD /d 4294967295 /f >nul
sc config WebClient start= auto >nul
net stop WebClient >nul 2>&1
net start WebClient >nul 2>&1

net use %DRIVE% /delete /y >nul 2>&1
echo Enter the SFTPGo password for user woomoo when prompted.
net use %DRIVE% "%WEBDAV%" /user:woomoo * /persistent:yes

if errorlevel 1 (
  echo Mapping failed. Check network access and the password.
  pause
  exit /b 1
)

echo restricted-node-6dc3f140f9 WebDAV is persistently mapped as %DRIVE%.
echo If restricted-node-6dc3f140f9 is offline, Windows will keep the drive and show it as disconnected.
pause
