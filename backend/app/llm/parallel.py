"""Bounded provider concurrency and isolated worker clients; no campaign writes in workers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock

_lock = Lock()
_slots = {}


@contextmanager
def campaign_slot(campaign_id):
    if campaign_id is None:
        yield
        return
    with _lock:
        entry = _slots.setdefault(campaign_id, [BoundedSemaphore(3), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _lock:
            entry[1] -= 1
            if not entry[1]:
                del _slots[campaign_id]


@contextmanager
def worker_client(client):
    # Scripted/injected clients remain usable in deterministic offline tests.
    from app.llm.client import TrackedOpenAI
    owned = isinstance(client, TrackedOpenAI) and client._owns_sdk
    child = client.fork() if owned else client
    try:
        yield child
    finally:
        if owned:
            child.close()


def run_jobs(client, jobs):
    """Return ordered outcomes, retaining sibling successes when one worker fails."""
    def run(job):
        try:
            with worker_client(client) as child:
                return job(child), None
        except Exception as exc:  # noqa: BLE001 - all outcomes must be joined before the coordinator raises
            return None, exc
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(run, jobs))
