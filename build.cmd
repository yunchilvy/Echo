@echo off
rem Echo 打包：单文件 exe，产物在 dist\Echo.exe
python -c "from core.winicon import write_ico; write_ico(r'docs/icon.ico')" || goto :err
python -m PyInstaller Echo.spec --noconfirm || goto :err
echo.
echo 打包完成: dist\Echo.exe
goto :eof

:err
echo 打包失败，检查是否已 pip install pyinstaller
exit /b 1
