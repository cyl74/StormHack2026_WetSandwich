from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.character_sim import World, SpawnQueue
from src.doodle_ai.gemini_analyzer import analyze_image
from src.doodle_ai.spawn_adapter import enqueue_character
from src.doodle_ai.trait_extractor import extract_traits

DEFAULT_IMAGE_PATH = "/Users/naman/Desktop/doodle.png"


def run_pipeline(image_path: str) -> None:
    print("\n=== 1. Analyzing doodle with Gemini ===")
    analysis = analyze_image(image_path)
    print(json.dumps(analysis, indent=2))

    print("\n=== 2. Extracting traits from Gemini output ===")
    traits = extract_traits(analysis)
    print(json.dumps({
        "emotion": traits["emotion"],
        "size": traits["size"],
        "passivity": traits["passivity"],
        "laziness": traits["laziness"],
        "base": {
            "speed": traits["base"].speed,
            "damage": traits["base"].damage,
            "health": traits["base"].health,
        },
        "traits": traits["traits"],
    }, indent=2))

    print("\n=== 3. Enqueueing character into SpawnQueue ===")
    queue = SpawnQueue(":memory:")
    cid = enqueue_character(queue, image_path, traits)
    print(f"SpawnQueue ID: {cid}")

    print("\n=== 4. Claiming character and spawning into World ===")
    world = World(seed=1)
    claimed = queue.claim(world.free_slots)
    for ch in claimed:
        world.spawn(ch)
        print(f"Spawned character {ch.id} at ({ch.x:.1f}, {ch.y:.1f})")

    print("\n=== 5. Running simulation for 3 seconds ===")
    for _ in range(30):
        world.step(0.1)

    print("\n=== 6. Snapshot (what the frontend would receive) ===")
    snapshot = world.snapshot()
    print(json.dumps(snapshot, indent=2))

    print("\n=== 7. Events (attacks, despawns, etc.) ===")
    events = world.pop_events()
    print(json.dumps(events, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the doodle-to-character pipeline.")
    parser.add_argument(
        "image_path",
        nargs="?",
        default=DEFAULT_IMAGE_PATH,
        help="Path to the doodle image to process.",
    )
    args = parser.parse_args(argv)

    path = args.image_path
    if not Path(path).exists():
        print(f"Image not found: {path}")
        return 1

    run_pipeline(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
