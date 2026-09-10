import threading
from pathlib import Path

from llama_cpp import Llama

ROOT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT_DIR / "AI" / "models" / "Qwen3-8B-Q4_K_M.gguf"

_lock = threading.RLock()
_llm = None


def get_llm():
    global _llm
    with _lock:
        if _llm is None:
            if not MODEL_PATH.exists():
                raise FileNotFoundError(f"Model not found: {MODEL_PATH}")
            _llm = Llama(
                model_path=str(MODEL_PATH),
                n_gpu_layers=-1,
                # Qwen3-8B was trained on a 32768-token context. The old
                # value here (4096) was smaller than a single tender text
                # chunk (MAX_CHUNK_CHARS=12000 chars is ~3000-3400 tokens
                # before the prompt/schema text is even added), so the
                # model had almost no budget left to write the JSON
                # response and, under grammar-constrained JSON decoding,
                # silently closed out with an empty "[]"/"{}" instead of
                # erroring. Match the model's trained context so there is
                # always room for the completion.
                n_ctx=8192,
                verbose=False,
            )
        return _llm


def reset_context():
    with _lock:
        if _llm is not None:
            try:
                _llm.reset()
            except Exception:
                pass


def model_loaded():
    with _lock:
        return _llm is not None
