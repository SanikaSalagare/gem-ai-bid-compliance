@echo off
echo =======================================================
echo ========= MAKE SURE A AI MODEL IS INSTALLED ===========
echo =======================================================
call .\.venv\Scripts\activate.bat

echo.
echo.
echo.
python --version

echo.
echo.
echo.
nvidia-smi

echo.
echo.
echo.
nvcc --version

python runserver.py runserver
pause
