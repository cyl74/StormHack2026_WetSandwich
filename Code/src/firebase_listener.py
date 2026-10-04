# src/firebase_listener.py

from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

try:
    from firebase_admin import firestore
except ModuleNotFoundError:  # pragma: no cover - demo mode fallback
    firestore = None

from src.character_sim import World, SpawnQueue, Stats, poll_spawns
from src.doodle_ai.gemini_analyzer import analyze_image
from src.doodle_ai.trait_extractor import extract_traits
from src.firestore_store import FirestoreSpawnQueue, init_firestore


def process_new_character(doc_id: str, data: dict, queue: FirestoreSpawnQueue):
    print(f"[Listener] New Firestore character: {doc_id}")

    image_doc = queue.images.document(doc_id).get()
    if not image_doc.exists:
        print(f"[Listener] ERROR: image doc missing for {doc_id}")
        return

    raw_bytes = bytes(image_doc.to_dict()["data"])
    analysis = analyze_image_from_bytes(raw_bytes)
    traits = extract_traits(analysis)

    queue.queue.document(doc_id).update({
        "emotion": traits["emotion"],
        "size": traits["size"],
        "passivity": traits["passivity"],
        "laziness": traits["laziness"],
        "base_health": traits["base"].health,
        "base_speed": traits["base"].speed,
        "base_damage": traits["base"].damage,
        "traits": traits["traits"],
        "spawned": False,
        "ai_processed": True,
    })

    print(f"[Listener] Processed {doc_id} with Gemini")


def analyze_image_from_bytes(raw_bytes: bytes):
    """Helper: Gemini analyzer expects a file path, so write temp file."""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
    tmp.write(raw_bytes)
    tmp.close()
    return analyze_image(tmp.name)


def listener_callback(col_snapshot, changes, read_time, queue):
    for change in changes:
        if change.type.name == "ADDED":
            doc = change.document
            data = doc.to_dict()
            if not data.get("ai_processed", False):
                threading.Thread(
                    target=process_new_character,
                    args=(doc.id, data, queue),
                    daemon=True,
                ).start()


def run_demo_pipeline(image_path: str | None = None) -> int:
    image_path = image_path or os.environ.get("DEMO_IMAGE_PATH") or "/Users/naman/Desktop/doodle.png"
    if not Path(image_path).exists():
        print(f"[Demo] Image not found: {image_path}")
        return 1

    print(f"[Demo] Loading image: {image_path}")
    analysis = analyze_image(image_path)
    traits = extract_traits(analysis)
    queue = SpawnQueue(":memory:")
    queue.enqueue(
        image=image_path,
        emotion=traits["emotion"],
        size=traits["size"],
        passivity=traits["passivity"],
        laziness=traits["laziness"],
        base=Stats(speed=traits["base"].speed, damage=traits["base"].damage, health=traits["base"].health),
        traits=traits["traits"],
    )

    world = World(seed=1)
    spawned = poll_spawns(world, queue)
    print(f"[Demo] Spawned {spawned} character(s) into simulation")
    world.step(0.1)
    print("[Demo] Snapshot:")
    print(world.snapshot())
    return 0


def start_listener(use_demo: bool = False):
    if use_demo or not (os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or os.getenv("FIREBASE_EMULATOR_HOST")):
        print("[Listener] Firestore not configured; starting local demo mode.")
        return run_demo_pipeline()

    print("[Listener] Initializing Firestore...")
    init_firestore()

    queue = FirestoreSpawnQueue()
    db = firestore.client()

    print("[Listener] Watching Firestore spawn_queue...")
    db.collection("spawn_queue").on_snapshot(
        lambda snap, changes, time: listener_callback(snap, changes, time, queue)
    )

    world = World(seed=1)
    print("[Simulation] Running world loop...")
    while True:
        poll_spawns(world, queue)
        world.step(0.1)
        time.sleep(0.1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the Firestore listener and simulation workflow.")
    parser.add_argument("--demo", action="store_true", help="Run in local demo mode without Firestore.")
    parser.add_argument("--image-path", default=None, help="Override the demo image path.")
    args = parser.parse_args(argv)
    return start_listener(use_demo=args.demo) if not args.image_path else run_demo_pipeline(args.image_path)


if __name__ == "__main__":
    raise SystemExit(main())