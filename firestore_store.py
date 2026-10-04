"""
Firestore-only spawn queue: no Cloud Storage, no billing account needed.

Layout:
    character_images/<id>   { data: <bytes>, content_type: "image/png", created_at }
    spawn_queue/<id>        { emotion, size, traits, base stats, spawned, created_at }

Images live in their own collection (not inside the queue doc) so that purging claimed
queue docs does not delete the image of a character that is still alive.

Same interface as SpawnQueue in character_sim.py (enqueue / claim / purge_spawned / pending).

Setup:
    pip install firebase-admin            # and optionally: pip install pillow
    export GOOGLE_APPLICATION_CREDENTIALS=secrets/serviceAccountKey.json
"""
from __future__ import annotations

import io
from collections import OrderedDict

import firebase_admin
from firebase_admin import credentials, firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from char_sim import Character, Stats, clamp, valid_traits

try:
    from PIL import Image
except ImportError:  # Pillow is optional, see prepare_image()
    Image = None

BATCH_LIMIT = 500                    # Firestore max writes per batch
MAX_UPLOAD_BYTES = 5 * 1024 * 1024   # reject anything bigger before even decoding it
MAX_IMAGE_BYTES = 300 * 1024         # stored size cap (Firestore doc limit is 1 MiB)
MAX_SIDE_PX = 128                    # sprites are shrunk to fit in 128x128
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def init_firestore(cred_path: str | None = None) -> None:
    if firebase_admin._apps:
        return
    cred = (credentials.Certificate(cred_path) if cred_path
            else credentials.ApplicationDefault())
    firebase_admin.initialize_app(cred)


def prepare_image(raw: bytes, max_side: int = MAX_SIDE_PX,
                  max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """Validate and shrink an uploaded drawing. Returns (png_bytes, content_type).

    With Pillow installed: decodes the image (proving it is a real image), shrinks it to
    fit max_side x max_side, strips metadata, and re-encodes as PNG.
    Without Pillow: only checks the PNG header and the size, so the frontend must
    already send a small PNG.
    """
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("image upload too large")

    if Image is None:
        if not raw.startswith(PNG_MAGIC):
            raise ValueError("only PNG images are supported (install pillow for more formats)")
        if len(raw) > max_bytes:
            raise ValueError(f"image is {len(raw)} bytes; max is {max_bytes} (install pillow to auto-shrink)")
        return raw, "image/png"

    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as e:
        raise ValueError(f"not a valid image: {e}") from e
    img = img.convert("RGBA")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    data = out.getvalue()
    if len(data) > max_bytes:
        raise ValueError(f"image is still {len(data)} bytes after shrinking; max is {max_bytes}")
    return data, "image/png"


class FirestoreSpawnQueue:
    QUEUE = "spawn_queue"
    IMAGES = "character_images"

    def __init__(self, image_cache_size: int = 2000) -> None:
        self.db = firestore.client()
        self.queue = self.db.collection(self.QUEUE)
        self.images = self.db.collection(self.IMAGES)
        # Serving 1000 characters to many clients would burn through the free read
        # quota if every request hit Firestore, so keep recently used images in memory.
        self._cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._cache_size = image_cache_size

    # -- write path (called when the frontend POSTs a new drawing) ----------- #
    def enqueue(self, *, image_bytes: bytes, emotion: str, size: float, base: Stats,  traits,
                name: str = "", passivity: float = 0.5, laziness: float = 0.3) -> str:
        data, content_type = prepare_image(image_bytes)   # raises ValueError if bad

        queue_ref = self.queue.document()                 # auto-generated id
        image_ref = self.images.document(queue_ref.id)    # same id, so they are linked

        # one atomic batch: either both docs exist or neither does
        batch = self.db.batch()
        batch.set(image_ref, {
            "data": data,
            "content_type": content_type,
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        batch.set(queue_ref, {
            "name": name,
            "emotion": emotion,
            "size": clamp(size, 0.5, 2.0),
            "passivity": clamp(passivity, 0, 1),
            "laziness": clamp(laziness, 0, 1),
            "base_health": base.health,
            "base_speed": base.speed,
            "base_damage": base.damage,
            "traits": valid_traits(traits),
            "spawned": False,
            "created_at": firestore.SERVER_TIMESTAMP,
        })
        batch.commit()
        self._remember(queue_ref.id, data, content_type)
        return queue_ref.id

    # -- read path (called by the simulation server every ~15 s) ------------- #
    def claim(self, limit: int) -> list[Character]:
        if limit <= 0:
            return []
        docs = list(self.queue.where(filter=FieldFilter("spawned", "==", False))
                    .limit(limit).stream())
        for i in range(0, len(docs), BATCH_LIMIT):
            batch = self.db.batch()
            for d in docs[i:i + BATCH_LIMIT]:
                batch.update(d.reference, {"spawned": True})
            batch.commit()
        return [self._to_character(d) for d in docs]

    @staticmethod
    def _to_character(doc) -> Character:
        d = doc.to_dict()
        return Character(
            id=doc.id, image=doc.id,         # image = id of the character_images doc
            emotion=d["emotion"], size=d["size"],
            passivity=d["passivity"], laziness=d["laziness"],
            base=Stats(speed=d["base_speed"], damage=d["base_damage"],
                       health=d["base_health"]),
            traits=d["traits"],
        )

    # -- serving images to clients ------------------------------------------ #
    def get_image(self, char_id: str) -> tuple[bytes, str] | None:
        """Return (png_bytes, content_type), or None if it doesn't exist.
        Wire this to an HTTP endpoint, e.g. GET /images/<id>, and send a long
        Cache-Control header so browsers don't re-request it."""
        if char_id in self._cache:
            self._cache.move_to_end(char_id)
            return self._cache[char_id]
        snap = self.images.document(char_id).get()
        if not snap.exists:
            return None
        d = snap.to_dict()
        data, ctype = bytes(d["data"]), d.get("content_type", "image/png")
        self._remember(char_id, data, ctype)
        return data, ctype

    def _remember(self, char_id: str, data: bytes, ctype: str) -> None:
        self._cache[char_id] = (data, ctype)
        self._cache.move_to_end(char_id)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    # -- cleanup ------------------------------------------------------------- #
    def purge_spawned(self) -> int:
        """Delete queue docs that were already claimed. Images stay: live characters need them."""
        docs = list(self.queue.where(filter=FieldFilter("spawned", "==", True)).stream())
        for i in range(0, len(docs), BATCH_LIMIT):
            batch = self.db.batch()
            for d in docs[i:i + BATCH_LIMIT]:
                batch.delete(d.reference)
            batch.commit()
        return len(docs)

    def delete_image(self, char_id: str) -> None:
        """Call when a character despawns (see the ("despawn", id, reason) world event)."""
        self.images.document(char_id).delete()
        self._cache.pop(char_id, None)

    def pending(self) -> int:
        q = self.queue.where(filter=FieldFilter("spawned", "==", False)).count()
        return q.get()[0][0].value


# Example wiring:
#
#   init_firestore()
#   queue = FirestoreSpawnQueue()
#   queue.enqueue(image_bytes=png_bytes, emotion="anger", size=1.2,
#                 base=Stats(speed=6, damage=7, health=80), traits=["aggressive"])
#
#   world = World()
#   run(world, queue, seconds=60)            # from character_sim
#
#   for ev in world.pop_events():
#       if ev[0] == "despawn":
#           queue.delete_image(ev[1])
#
#   # in your web handler:  data, ctype = queue.get_image(char_id)