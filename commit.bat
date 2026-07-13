@echo off
chcp 65001 > nul
echo.
echo ======================================
echo  Sincronizando arquivos para o GitHub
echo ======================================
echo.

set SRC_API=C:\Users\victor.ferreira\Clinica CDE\Financeiro - Documentos\03. CONTROLADORIA\31. IMPLEMENTAÇÕES E AUTOMAÇÕES\Acesso Claude SQL\main.py
set SRC_DASH=C:\Users\victor.ferreira\Clinica CDE\Financeiro - Documentos\03. CONTROLADORIA\31. IMPLEMENTAÇÕES E AUTOMAÇÕES\DRE\dashboard_cde_v3.html
set SRC_README=C:\Users\victor.ferreira\Documents\Claude\Projects\P&L Visualization\README.md
set REPO=C:\CDE-DRE

echo Copiando arquivos atualizados...
copy /Y "%SRC_API%"   "%REPO%\api\main.py"     > nul
copy /Y "%SRC_DASH%"  "%REPO%\dashboard\dashboard_cde_v3.html" > nul
copy /Y "%SRC_README%" "%REPO%\README.md"       > nul

cd /d "%REPO%"

git add -A

git diff --cached --quiet
if %ERRORLEVEL% == 0 (
    echo Nenhuma mudanca detectada. Nada para commitar.
    pause
    exit /b 0
)

set /p MSG=Descricao do commit (Enter para "update"):
if "%MSG%"=="" set MSG=update

git commit -m "%MSG%"
git push

echo.
echo ======================================
echo  Pronto! Publicado no GitHub.
echo  https://github.com/ViktorKid/P-L---CDE
echo ======================================
echo.
pause
