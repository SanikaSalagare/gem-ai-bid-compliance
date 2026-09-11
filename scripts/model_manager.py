import threading
from pathlib import Path

from llama_cpp import Llama

ROOT_DIR = Path(__file__).resolve().parent.parent
MODEL_PATH = ROOT_DIR / "AI" / "models" / "Qwen3-8B-Q4_K_M.gguf"

_llm = None

# Guards every operation that touches the shared Llama instance's mutable
# state: both create_chat_completion(...) calls AND reset(). A single
# llama_cpp Llama instance is not safe to call concurrently from multiple
# threads, and that includes reset() racing with an in-flight completion,
# not just two completions racing each other.
#
# This used to be two separate locks: `_lock` guarded get_llm()/reset(),
# while callers wrapped only their create_chat_completion() calls in a
# second lock (LLM_LOCK). That left a gap - reset_context() could run
# (via `_lock`) at the same time another thread was mid-generation inside
# `with LLM_LOCK: llm.create_chat_completion(...)`, since the two calls
# used different locks and didn't exclude each other. In practice this is
# exactly what happened when tender-document processing
# (scripts/tender_queue.py, which calls reset_context() between chunks)
# and bid-compliance analysis (scripts/compliance_queue.py) ran on their
# own background threads at the same time: one job's reset() could land
# mid-generation of the other job, corrupting or truncating whichever one
# was mid-completion. reset_context() now takes this same lock, so it can
# never run while another thread's create_chat_completion() is in
# flight - both operations serialize together.
#
# As of this fix, scripts/job_queue.py also ensures only one background
# job (tender processing OR bid compliance) ever runs at a time, so this
# lock is now a second, independent guarantee rather than the only thing
# preventing interleaved LLM calls - see job_queue.py for details.
LLM_LOCK = threading.RLock()


def get_llm():
    global _llm
    with LLM_LOCK:
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
    with LLM_LOCK:
        if _llm is not None:
            try:
                _llm.reset()
            except Exception:
                pass


def model_loaded():
    with LLM_LOCK:
        return _llm is not None
