@echo off
title PC-98 Cyber Launcher
mode con: cols=96 lines=34 >nul 2>nul
python "%~dp0scripts\game-launcher.py" %*
