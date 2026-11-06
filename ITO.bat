@echo off
rem ITO sesli ev asistanini baslatir. Cift tiklamak yeterli.
title ITO - Sesli Ev Asistani

rem 1) Bu dosyanin bulundugu klasor
set "PROJE=%~dp0"
if exist "%PROJE%.venv312\Scripts\python.exe" goto bulundu

rem 2) Dosya baska yere kopyalandiysa: projeyi Masaustunde ara
for /d %%D in ("%USERPROFILE%\Desktop\Smart Home*") do (
    if exist "%%~fD\ito-asistan\.venv312\Scripts\python.exe" set "PROJE=%%~fD\ito-asistan\"
)
if exist "%PROJE%.venv312\Scripts\python.exe" goto bulundu

echo Proje klasoru bulunamadi.
echo Bakilan yer: %~dp0
echo Masaustune bat dosyasinin kopyasini degil, KISAYOLUNU koymalisin.
pause
exit /b 1

:bulundu
cd /d "%PROJE%"
".venv312\Scripts\python.exe" main.py

echo.
echo ITO kapandi. Pencereyi kapatmak icin bir tusa bas.
pause >nul
