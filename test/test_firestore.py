"""
Smoke test for the Firestore-only setup (images stored in Firestore, no Cloud Storage).

Usage:
    export GOOGLE_APPLICATION_CREDENTIALS=secrets/serviceAccountKey.json
    python test_firestore.py            # runs the test, then cleans up after itself
    python test_firestore.py --keep     # leave the test data so you can look at it in the console
"""
import os
import struct
import sys
import zlib

from char_sim import Stats, World, poll_spawns, run
from firestore_store import FirestoreSpawnQueue, init_firestore, prepare_image


def make_png(rgb=(220, 40, 40), size=16) -> bytes:
    """Build a tiny valid PNG (solid color square) with no image libraries."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))


def step(msg: str) -> None:
    print(f"\n==> {msg}")


def main() -> None:
    keep = "--keep" in sys.argv

    step("1. Image validation (no network needed)")
    good, ctype = prepare_image(make_png())
    print(f"  valid PNG accepted: {len(good)} bytes, {ctype}")
    for label, bad in [("garbage bytes", b"not an image at all"),
                       ("oversized upload", b"\x89PNG\r\n\x1a\n" + b"0" * (6 * 1024 * 1024))]:
        try:
            prepare_image(bad)
        except ValueError as e:
            print(f"  {label} rejected: {e}")
        else:
            raise AssertionError(f"{label} should have been rejected")

    step("2. Connecting to Firestore")
    init_firestore()
    queue = FirestoreSpawnQueue()
    print("  connected")

    step("3. Creating 3 test characters (image doc + queue doc, one atomic batch)")
    samples = [
        dict(emotion="anger", traits=["aggressive", "energetic"], rgb=(220, 40, 40)),
        dict(emotion="happiness", traits=["cheerful", "playful"], rgb=(240, 200, 40)),
        dict(emotion="fear", traits=["shy", "nervous"], rgb=(60, 90, 220)),
    ]
    ids = []
    for s in samples:
        cid = queue.enqueue(
            image_bytes=make_png(s["rgb"]), emotion=s["emotion"], size=1.0,
            base=Stats(speed=5, damage=5, health=70), traits=s["traits"])
        ids.append(cid)
        print("  created", cid, s["emotion"])

    step("4. Checking images are stored and round-trip correctly")
    for cid in ids:
        snap = queue.images.document(cid).get()
        assert snap.exists, f"missing image doc for {cid}"
        stored = bytes(snap.to_dict()["data"])
        assert stored.startswith(b"\x89PNG"), "stored data is not a PNG"
        print(f"  character_images/{cid}: {len(stored)} bytes OK")
    queue._cache.clear()                       # force a real read from Firestore
    data, ctype = queue.get_image(ids[0])
    assert data.startswith(b"\x89PNG") and ctype == "image/png"
    assert queue.get_image("does-not-exist") is None
    print("  get_image() works, missing id returns None")

    step("5. Checking the Firestore queue")
    pending = queue.pending()
    print("  pending docs:", pending)
    assert pending >= len(ids)

    step("6. Claiming into the simulation")
    world = World(seed=1)
    spawned = poll_spawns(world, queue)
    print("  spawned:", spawned)
    assert spawned >= len(ids)
    for cid in ids:
        ch = world.chars[cid]
        print(f"  {cid}: emotion={ch.emotion} hp={ch.hp:.0f} image_id={ch.image}")
    assert queue.pending() == 0, "claimed docs should be marked spawned"

    step("7. Running 10 simulated seconds")
    run(world, queue, seconds=10)
    print("  alive:", len(world.chars))
    print("  example snapshot:", world.snapshot()[0])

    if keep:
        print("\n--keep set: test data left in place. Check the console:")
        print("  Firestore -> character_images (3 docs with a `data` bytes field)")
        print("  Firestore -> spawn_queue (claimed docs get removed on the next purge)")
        return

    step("8. Cleaning up")
    print("  purged queue docs:", queue.purge_spawned())
    for cid in ids:
        queue.delete_image(cid)
    assert not queue.images.document(ids[0]).get().exists
    print("  deleted test images")
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        sys.exit("Set GOOGLE_APPLICATION_CREDENTIALS first.")
    main()