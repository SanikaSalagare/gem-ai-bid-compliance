"""Single shared background worker for every LLM-consuming job.

scripts/tender_queue.py (document processing + requirement extraction)
and scripts/compliance_queue.py (bid compliance analysis) both used to
run their own background worker thread. That meant a tender-processing
job and a bid-compliance job could genuinely execute at the same time,
each on its own thread, both calling into the one shared Llama instance
in scripts/model_manager.py. Even with locking around individual
create_chat_completion() calls, this is what caused the actual bug: if
a bidder submitted their bid for compliance analysis while its tender's
documents were still being processed, the two jobs stepped on each
other and the tender's processing run would come out incomplete (or
vice versa).

The fix here is architectural rather than just more locking: every job -
regardless of which queue module submitted it - is funneled through this
one module's single queue and single worker thread. Jobs run strictly
one at a time, in the order they were submitted, so a tender's
processing run and any bid-compliance run (for that tender or any
other) can never overlap. model_manager.LLM_LOCK is kept as a second,
independent safeguard, but with only one job ever executing at a time
it should no longer be load-bearing on its own.
"""

from __future__ import annotations

import queue
import threading
import traceback
from typing import Callable

_jobs: "queue.Queue[Callable[[], None]]" = queue.Queue()
_worker_lock = threading.Lock()
_worker_started = False


def _worker() -> None:
    # Imported lazily so importing this module never forces the (slow,
    # optional-at-import-time) llama_cpp dependency to load.
    from scripts.model_manager import reset_context

    while True:
        run = _jobs.get()
        try:
            run()
        except Exception:
            # Individual jobs are expected to catch and record their own
            # failures (see tender_queue._worker_run / compliance_queue
            # equivalents); this is a last-resort backstop so a bug in a
            # job's own error handling can never kill the worker thread.
            traceback.print_exc()
        finally:
            # Always leave the shared model in a clean state for
            # whichever job runs next, regardless of how this one ended.
            reset_context()
            _jobs.task_done()


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            thread = threading.Thread(target=_worker, name="gem-llm-worker", daemon=True)
            thread.start()
            _worker_started = True


def submit(run: Callable[[], None]) -> None:
    """Queue `run` (a zero-argument callable) for execution on the single
    shared worker thread. Jobs execute strictly one at a time, in
    submission order, no matter which module or queue submitted them."""
    _ensure_worker()
    _jobs.put(run)
