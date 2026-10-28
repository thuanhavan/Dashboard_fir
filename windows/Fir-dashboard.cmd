@echo off
title Fir dashboard
setlocal
set "CFG=%APPDATA%\fir_dashboard_user.txt"
set "OUT=%TEMP%\fir_dashboard.html"
set "TOOL=/project/def-kanj241/hvt632/fir_interface/bin/firdash"

if exist "%CFG%" set /p FIRUSER=<"%CFG%"
if not defined FIRUSER goto ask
goto run

:ask
echo.
set /p FIRUSER=Your Fir username (for example hvt632): 
if not defined FIRUSER goto ask
echo %FIRUSER%| findstr /r /x "[a-z][a-z0-9_-]*" >nul || (echo Username can only contain small letters and numbers. & set "FIRUSER=" & goto ask)
> "%CFG%" echo %FIRUSER%

:run
echo.
echo Signing in to Fir as %FIRUSER%.
echo Approve the Duo push on your phone, then wait about 20 seconds...
echo.
ssh %FIRUSER%@fir.alliancecan.ca "bash -lc '%TOOL% --html -'" > "%OUT%"
for %%F in ("%OUT%") do if %%~zF LSS 1000 goto fail
start "" "%OUT%"
exit /b 0

:fail
echo.
echo Could not load the dashboard. Check the username and your Duo approval.
echo The username will be asked again next time.
del "%CFG%" 2>nul
pause
exit /b 1
