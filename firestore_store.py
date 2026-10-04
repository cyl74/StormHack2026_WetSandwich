# src/firebase_listener.py

from __future__ import annotations

import threading
import time
import traceback

from firebase_admin import firestore

from src.firestore_store import init_firestore

#! method1 --> naman's ai stuff
#! method2 --> char sim stuff

# --- Processing ---------------------------------------------------------------

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()


def process_spawn(doc_ref, doc_id: str, data: dict) -> None:
    # Guard against duplicate delivery of the same doc while it's being processed
    with _in_flight_lock:
        if doc_id in _in_flight:
            return
        _in_flight.add(doc_id)

    try:
        print(f"[Listener] New spawn: {doc_id}")

        # ok1 = method1(doc_id, data) is not False
        ok1 = True
        # ok2 = method2(doc_id, data) is not False
        
        if ok1 and ok2:
            doc_ref.delete()
            print(f"[Listener] {doc_id}: both methods succeeded, entry deleted")
        else:
            print(f"[Listener] {doc_id}: method1={ok1}, method2={ok2}; entry kept")
    except Exception:
        print(f"[Listener] {doc_id}: error, entry kept")
        traceback.print_exc()
    finally:
        with _in_flight_lock:
            _in_flight.discard(doc_id)


def listener_callback(col_snapshot, changes, read_time):
    for change in changes:
        if change.type.name == "ADDED":
            doc = change.document
            threading.Thread(
                target=process_spawn,
                args=(doc.reference, doc.id, doc.to_dict() or {}),
                daemon=True,
            ).start()


def start_listener():
    print("[Listener] Initializing Firestore...")
    init_firestore()
    db = firestore.client()

    print("[Listener] Watching 'spawns' collection...")
    watch = db.collection("spawns").on_snapshot(listener_callback)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        watch.unsubscribe()


if __name__ == "__main__":
    start_listener()