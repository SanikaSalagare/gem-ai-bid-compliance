@echo off
mkdir cache
py -3.11 -m venv .venv
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

curl -L -o ".\cache\llama_cpp_python-0.3.20+cuda13.0.sm86.ampere-py3-none-win_amd64.whl" "https://github.com/dougeeai/llama-cpp-python-wheels/releases/download/v0.3.20-cuda13.0-sm86/llama_cpp_python-0.3.20+cuda13.0.sm86.ampere-py3-none-win_amd64.whl"
python -m pip install ".\cache\llama_cpp_python-0.3.20+cuda13.0.sm86.ampere-py3-none-win_amd64.whl"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo.
echo.
echo =======================================================
echo =========== MAKE SURE TO INSTALL AI MODELS ============
echo =======================================================
pause