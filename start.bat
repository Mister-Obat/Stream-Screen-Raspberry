@echo off
cd /d "%~dp0"
echo Verification des dependances...
py -m pip install -r requirements.txt
cls
start "" "StreamScreen.pyw"
exit
