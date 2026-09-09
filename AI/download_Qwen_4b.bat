@echo off
mkdir models
echo ==========================================
echo Downloading Qwen3-4B Q4_K_M GGUF
echo ==========================================

curl -L -o models/Qwen3-4B-Q4_K_M.gguf "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"

