@echo off
title Universal Game Translator
mode con: cols=110 lines=32 >nul 2>nul
python "%~dp0scripts\universal-launcher.py" %*
