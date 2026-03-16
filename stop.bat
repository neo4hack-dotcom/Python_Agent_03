@echo off
chcp 65001 >nul 2>&1

:: ============================================================
::  Arreter tous les processus Python Agent Platform
:: ============================================================

title Arret -- Agent Platform

echo.
echo  Arret de Python Agent Platform...
echo.

:: Tuer uvicorn sur le port 8000
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo  Arret du processus PID %%P (port 8000)...
    taskkill /PID %%P /F >nul 2>&1
)

:: Tuer le serveur Vite sur le port 3000
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":3000" ^| findstr "LISTENING"') do (
    echo  Arret du processus PID %%P (port 3000)...
    taskkill /PID %%P /F >nul 2>&1
)

echo.
echo  Termine.
timeout /t 2 /nobreak >nul
