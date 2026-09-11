import threading
from pathlib import Path

from llama_cpp import Llama

ROOT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT_DIR / "AI" / "models" / "Qwen3-8B-Q4_K_M.gguf"

_lock = threading.RLock()
_llm = None

# Guards actual inference calls (llm.create_chat_completion(...)), as
# opposed to `_lock` above which only guards loading/resetting the model.
# There are now two independent background workers that can want the LLM
# at the same time - scripts/tender_queue.py (requirement extraction) and
# scripts/compliance_queue.py (bid evaluation) - and a single llama_cpp
# Llama instance is not safe to call concurrently from multiple threads.
# Callers must hold this for the duration of each create_chat_completion
# call (see requirement_detector.extract_chunk_requirements and
# compliance_checker.evaluate_requirement).
LLM_LOCK = threading.RLock()


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
                n_ctx=32768,
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
