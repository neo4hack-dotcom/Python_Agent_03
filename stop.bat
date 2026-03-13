@echo off
chcp 65001 >nul 2>&1

:: ============================================================
::  Arrêter tous les processus Python Agent Platform
:: ============================================================

title Arrêt — Agent Platform

echo.
echo  Arrêt de Python Agent Platform...
echo.

:: Tuer uvicorn sur le port 8000
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    echo  Arrêt du processus PID %%P (port 8000)...
    taskkill /PID %%P /F >nul 2>&1
)

:: Tuer le serveur Vite sur le port 3000
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":3000" ^| findstr "LISTENING"') do (
    echo  Arrêt du processus PID %%P (port 3000)...
    taskkill /PID %%P /F >nul 2>&1
)

echo.
echo  Terminé.
timeout /t 2 /nobreak >nul
