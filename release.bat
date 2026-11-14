@echo off
rem ---------------------------------------------------------------------
rem Builds a copy of DashX360Native that runs on a machine with no Visual
rem Studio and no VC++ redistributable installed, and stages it in dist\.
rem
rem The msvcp140d.dll your friend saw is the DEBUG C++ runtime. The trailing
rem "d" is the whole story: debug CRTs are not redistributable and ship only
rem with Visual Studio, so a Debug build can never run on a machine that
rem doesn't have VS on it. Two changes fix it for good:
rem
rem   1. --config Release, so it links the retail CRT rather than the debug one.
rem   2. CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded (/MT), which links that CRT
rem      STATICALLY -- so the exe needs neither msvcp140.dll nor
rem      vcruntime140.dll nor the redistributable installer at all.
rem
rem CMAKE_POLICY_DEFAULT_CMP0091=NEW is what makes setting #2 take effect on a
rem project whose cmake_minimum_required predates 3.15; harmless otherwise.
rem Nothing in CMakeLists.txt needs editing -- both settings are passed here.
rem
rem Everything else the app touches at runtime is already safe to ship:
rem XInput is resolved with LoadLibrary at startup (Gamepad.cpp tries 1_4,
rem 1_3, 9_1_0 in turn), and d3d11 / dxgi / d3dcompiler_47 are all in-box on
rem Windows 8.1 and later.
rem ---------------------------------------------------------------------
setlocal enabledelayedexpansion
cd /d "%~dp0"

where cmake >nul 2>&1
if errorlevel 1 (
    echo [!] cmake is not on PATH. Open "Developer Command Prompt for VS"
    echo     and run this script from there.
    exit /b 1
)

echo === configuring ^(Release, static CRT^)
cmake -S . -B build-release ^
    -DCMAKE_POLICY_DEFAULT_CMP0091=NEW ^
    -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded
if errorlevel 1 exit /b 1

echo === building
cmake --build build-release --config Release
if errorlevel 1 exit /b 1

rem The Visual Studio generator is multi-config and CMakeLists sets no output
rem directory, so the exe lands in the per-config subfolder. (Searching for it
rem with "for /r" and a plain file name does not work: that form names the
rem file in every subfolder whether or not it is there.)
set "EXE=build-release\Release\DashX360Native.exe"
if not exist "%EXE%" (
    echo [!] built, but %EXE% is not there
    exit /b 1
)

echo === staging dist\
if exist dist rmdir /s /q dist
mkdir dist
copy /y "%EXE%" "dist\" >nul
if errorlevel 1 (
    echo [!] couldn't copy %EXE% into dist\
    exit /b 1
)
rem Left out: scenes\_to_delete, and video\ -- the boot video the app no
rem longer plays now that assets\bootanim is rendered instead. robocopy
rem reports success as any exit code below 8.
robocopy assets "dist\assets" /e /xd _to_delete video /nfl /ndl /njh /njs /np >nul
if errorlevel 8 (
    echo [!] couldn't copy assets\ -- the app resolves assets\scenes and
    echo     assets\fonts relative to the exe, so it needs to sit alongside.
    exit /b 1
)

rem A statically linked exe contains no import entry for the CRT DLLs, so if
rem either name still appears as a string in the binary, the static-CRT switch
rem didn't take and the build would fail on your friend's machine again.
echo === checking for a CRT dependency
set "BAD="
findstr /m /c:"MSVCP140" "dist\DashX360Native.exe" >nul 2>&1 && set "BAD=1"
findstr /m /c:"VCRUNTIME140" "dist\DashX360Native.exe" >nul 2>&1 && set "BAD=1"
if defined BAD (
    echo [!] the exe still references the C++ runtime DLLs. The static-CRT
    echo     setting didn't apply -- check whether CMakeLists.txt sets
    echo     CMAKE_MSVC_RUNTIME_LIBRARY or MSVC_RUNTIME_LIBRARY itself, which
    echo     would override what this script passes in.
    exit /b 1
)

echo.
echo     OK: dist\DashX360Native.exe  ^(Release, static CRT^)
echo     Zip the whole dist\ folder -- exe and assets\ together -- and send that.
echo.
endlocal