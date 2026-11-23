@echo off
rem Запуск установщика из Проводника: двойной щелчок по этому файлу.
rem Сам установщик — ragraf-setup.ps1 рядом; здесь только вызов pwsh 7.6.
rem ВНИМАНИЕ: этот файл обязан остаться в CRLF — единственное исключение из правила LF.
setlocal
where pwsh >nul 2>nul
if errorlevel 1 (
    echo.
    echo ОТКАЗ: PowerShell 7.6 не найден в PATH.
    echo Поставьте PowerShell 7.6 ^(https://aka.ms/powershell^) и запустите снова.
    echo.
    pause
    exit /b 3
)
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0ragraf-setup.ps1" %*
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" (
    echo.
    echo Установка завершилась с кодом %CODE%. Подробности выше.
    pause
)
exit /b %CODE%
