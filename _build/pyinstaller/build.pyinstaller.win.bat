﻿@echo off

REM ......................setup variables......................

if [%1]==[] (
    SET ARCH=64
) else (
    SET ARCH=%1
)

if ["%ARCH%"]==["64"] (
    SET BINARCH=x64
    SET FFMPEG=https://ffmpeg.zeranoe.com/builds/win64/static/ffmpeg-latest-win64-static.7z
    SET MEDIAINFO_URL=https://mediaarea.net/download/binary/mediainfo/0.7.95/MediaInfo_CLI_0.7.95_Windows_x64.zip
    SET MEDIAINFO=MediaInfo_CLI_0.7.95_Windows_x64.zip
)
if ["%ARCH%"]==["32"] (
    SET BINARCH=x86
    SET FFMPEG=https://ffmpeg.zeranoe.com/builds/win32/static/ffmpeg-latest-win32-static.7z
    SET MEDIAINFO_URL=https://mediaarea.net/download/binary/mediainfo/0.7.95/MediaInfo_CLI_0.7.95_Windows_i386.zip
    SET MEDIAINFO=MediaInfo_CLI_0.7.95_Windows_i386.zip
)

REM ......................get latest version number......................

for /f "delims=" %%a in ('python version.py') do @set VERSION=%%a

REM ......................cleanup previous build scraps......................

rd /s /q build
rd /s /q dist
if not exist "..\..\bin\" mkdir ..\..\bin\
del /q ..\..\bin\*.*

REM ......................download latest FFmpeg static binary......................

if not exist ".\temp\" mkdir temp
if not exist "temp\ffmpeg-latest-win%ARCH%-static.7z" ( aria2c -d temp -x 6 %FFMPEG% )
if not exist "temp\%MEDIAINFO%" ( aria2c -d temp -x 6 %MEDIAINFO_URL% )

REM ......................extract ffmpeg.exe to its expected location......................

cd temp\
7z e ffmpeg-latest-win%ARCH%-static.7z ffmpeg-latest-win%ARCH%-static/bin/ffmpeg.exe
unzip %MEDIAINFO% MediaInfo.exe
if not exist "..\..\..\bin\" mkdir "..\..\..\bin\"
move ffmpeg.exe ..\..\..\bin\
move MediaInfo.exe ..\..\..\bin\
cd ..

REM ......................run pyinstaller......................

pyinstaller --clean vidcutter.win%ARCH%.spec

REM ......................add metadata to built Windows binary......................

verpatch dist\vidcutter.exe /va %VERSION%.0 /pv %VERSION%.0 /s desc "VidCutter" /s name "VidCutter" /s copyright "(c) 2017 Pete Alexandrou" /s product "VidCutter %BINARCH%" /s company "ozmartians.com"

REM ......................call Inno Setup installer build script......................

cd ..\InnoSetup
"C:\Program Files (x86)\Inno Setup 5\iscc.exe" installer_%BINARCH%.iss

cd ..\pyinstaller
