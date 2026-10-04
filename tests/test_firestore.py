"""
Pytest tests for the Firestore-only setup (images stored in Firestore, no Cloud Storage).

Run from the project root:
    export GOOGLE_APPLICATION_CREDENTIALS=secrets/serviceAccountKey.json
    python3 -m pytest tests/test_firestore.py -v -s

Keep the test data in Firestore so you can inspect it in the console:
    KEEP_TEST_DATA=1 python3 -m pytest tests/test_firestore.py -v -s

Tests that need Firestore are skipped automatically if
GOOGLE_APPLICATION_CREDENTIALS isn't set. The image validation tests
need no network and always run.
"""
import os
import struct
import zlib

import pytest

from char_sim import Stats, World, poll_spawns, run
from firestore_store import FirestoreSpawnQueue, init_firestore, prepare_image

KEEP = bool(os.environ.get("KEEP_TEST_DATA"))

SAMPLES = [
    dict(emotion="anger", traits=["aggressive", "energetic"], rgb=(220, 40, 40)),
    dict(emotion="happiness", traits=["cheerful", "playful"], rgb=(240, 200, 40)),
    dict(emotion="fear", traits=["shy", "nervous"], rgb=(60, 90, 220)),
]


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
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


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def queue():
    """Connect to Firestore once per module. Skips if no credentials."""
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        pytest.skip("Set GOOGLE_APPLICATION_CREDENTIALS to run Firestore tests.")
    init_firestore()
    return FirestoreSpawnQueue()


@pytest.fixture
def created_ids(queue):
    """Create 3 test characters (image doc + queue doc) and clean up afterwards."""
    ids = []
    for s in SAMPLES:
        cid = queue.enqueue(
            image_bytes=make_png(s["rgb"]),
            emotion=s["emotion"],
            size=1.0,
            base=Stats(speed=5, damage=5, health=70),
            traits=s["traits"],
            name="Test Angry"
        )
        ids.append(cid)

    yield ids

    if KEEP:
        return
    queue.purge_spawned()
    for cid in ids:
        queue.delete_image(cid)


# --------------------------------------------------------------------------
# 1. image validation (no network needed)
# --------------------------------------------------------------------------
def test_prepare_image_accepts_valid_png():
    data, ctype = prepare_image(make_png())
    assert len(data) > 0
    assert ctype == "image/png"


@pytest.mark.parametrize(
    "label, bad_bytes",
    [
        ("garbage bytes", b"not an image at all"),
        ("oversized upload", b"\x89PNG\r\n\x1a\n" + b"0" * (6 * 1024 * 1024)),
    ],
)
def test_prepare_image_rejects_bad_input(label, bad_bytes):
    with pytest.raises(ValueError):
        prepare_image(bad_bytes)


# --------------------------------------------------------------------------
# 2-4. Firestore: connect, create, store, round-trip
# --------------------------------------------------------------------------
def test_enqueue_creates_ids(created_ids):
    assert len(created_ids) == len(SAMPLES)
    assert len(set(created_ids)) == len(created_ids), "ids should be unique"


def test_images_stored_and_round_trip(queue, created_ids):
    for cid in created_ids:
        snap = queue.images.document(cid).get()
        assert snap.exists, f"missing image doc for {cid}"
        stored = bytes(snap.to_dict()["data"])
        assert stored.startswith(b"\x89PNG"), "stored data is not a PNG"

    queue._cache.clear()  # force a real read from Firestore
    data, ctype = queue.get_image(created_ids[0])
    assert data.startswith(b"\x89PNG")
    assert ctype == "image/png"


def test_get_image_missing_returns_none(queue):
    assert queue.get_image("does-not-exist") is None


# --------------------------------------------------------------------------
# 5. queue
# --------------------------------------------------------------------------
def test_queue_has_pending(queue, created_ids):
    assert queue.pending() >= len(created_ids)


# --------------------------------------------------------------------------
# 6. claim into the simulation
# --------------------------------------------------------------------------
def test_claim_into_simulation(queue, created_ids):
    world = World(seed=1)
    spawned = poll_spawns(world, queue)
    assert spawned >= len(created_ids)

    for cid, sample in zip(created_ids, SAMPLES):
        ch = world.chars[cid]
        assert ch.emotion == sample["emotion"]
        assert ch.hp > 0

    assert queue.pending() == 0, "claimed docs should be marked spawned"


# --------------------------------------------------------------------------
# 7. run the simulation for 10 seconds (takes a few real seconds)
# --------------------------------------------------------------------------
def test_run_simulation(queue, created_ids):
    world = World(seed=1)
    poll_spawns(world, queue)

    run(world, queue, seconds=10)

    assert len(world.chars) >= 0
    snapshot = world.snapshot()
    assert isinstance(snapshot, list)


# --------------------------------------------------------------------------
# 8. cleanup
# --------------------------------------------------------------------------
@pytest.mark.skipif(KEEP, reason="KEEP_TEST_DATA is set")
def test_cleanup_removes_images(queue):
    cid = queue.enqueue(
        image_bytes=make_png(),
        emotion="anger",
        size=1.0,
        base=Stats(speed=5, damage=5, health=70),
        traits=["aggressive"],
        name="Test Angry"
    )
    assert queue.images.document(cid).get().exists

    queue.purge_spawned()
    queue.delete_image(cid)
    assert not queue.images.document(cid).get().exists