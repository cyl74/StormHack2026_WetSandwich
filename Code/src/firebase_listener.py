"""
Event-driven spawn pipeline (replaces the 15-second poll).

    1. the frontend (app.js) adds a doc to the `spawns` collection:
         { name, title, emotion, size, image: <base64 PNG>, status: "pending", createdAt }
    2. on_snapshot fires for the new pending doc
    3. the AI step runs (MOCKED: hard-coded traits for now, see run_ai)
    4. a char_sim Character is built (the player's emotion + size override the AI's)
    5. the Firestore doc is deleted
    6. (Character, image bytes) is handed to the game through `pipeline.incoming`;
       the player's name/title go in `pipeline.meta[char_id]`

The game (game_view.py) owns the World and drains `incoming` on its own thread, so
nothing here ever touches the simulation. Because the image bytes travel with the
character, nothing needs to stay in Firestore after step 5.

Headless check (prints characters as they arrive, no window):
    python firebase_listener.py --demo
    python firebase_listener.py --cred secrets/serviceAccountKey.json
"""
from __future__ import annotations

import argparse
import base64
import queue
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from char_sim import EMOTIONS, TRAITS, Character, Stats, clamp, valid_traits


# --------------------------------------------------------------------------- #
# Step 3: the AI (mocked)
# --------------------------------------------------------------------------- #
def random_traits(rng: random.Random | None = None) -> dict:
    """Backup for the AI: 5 random traits, plus random stats so characters differ."""
    r = rng or random
    return {
        "emotion": r.choice(sorted(EMOTIONS)),
        "size": r.choice([0.5, 1.0, 1.5, 2.0]),
        "passivity": r.random(),
        "laziness": r.random(),
        "base": Stats(speed=r.randint(1, 10), damage=r.randint(1, 10),
                      health=r.randint(20, 100)),
        "traits": r.sample(list(TRAITS), min(5, len(TRAITS))),
    }


def ai_traits(image_bytes: bytes | None) -> dict:
    """Real AI goes here, returning the same shape as random_traits().
    Not wired up yet, so it raises and run_ai falls back to random_traits().

        analysis = analyze_image_from_bytes(image_bytes)   # Gemini
        return extract_traits(analysis)
    """
    raise NotImplementedError("AI not connected yet")


def run_ai(image_bytes: bytes | None) -> dict:
    try:
        return ai_traits(image_bytes)
    except Exception:                                  # AI missing or failed: use the backup
        return random_traits()


# --------------------------------------------------------------------------- #
# Step 4: traits -> char_sim Character
# --------------------------------------------------------------------------- #
def build_character(char_id: str, t: dict) -> Character:
    return Character(
        id=char_id, image=char_id,
        emotion=t["emotion"], size=clamp(t["size"], 0.5, 2.0),
        passivity=clamp(t["passivity"], 0, 1), laziness=clamp(t["laziness"], 0, 1),
        base=t["base"], traits=valid_traits(t["traits"]),
    )


# --------------------------------------------------------------------------- #
# The Firestore listener
# --------------------------------------------------------------------------- #
class SpawnPipeline:
    label = "listening to Firestore"
    COLLECTION = "spawns"                             # what app.js writes to

    def __init__(self, cred_path: str | None = None, workers: int = 4) -> None:
        self.incoming: queue.Queue = queue.Queue()    # (Character, png bytes | None)
        self.meta: dict[str, dict] = {}               # char id -> {"name", "title"}
        self.received = 0                             # characters delivered so far
        self.last_error: str | None = None
        self._in_flight = 0
        self._seen: set[str] = set()                  # on_snapshot can re-deliver docs
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers)
        self._cred = cred_path
        self._watch = None
        self.col = None

    @property
    def pending(self) -> int:
        """Drawings that are being processed or are waiting for the game to pick them up."""
        return self._in_flight + self.incoming.qsize()

    def start(self) -> None:
        from firebase_admin import firestore           # lazy: demo mode needs no firebase
        from google.cloud.firestore_v1.base_query import FieldFilter
        from firestore_store import init_firestore
        init_firestore(self._cred)
        self.col = firestore.client().collection(self.COLLECTION)
        # The first callback delivers every pending doc already there as ADDED, so
        # drawings submitted while the game was offline are picked up on startup.
        self._watch = (self.col.where(filter=FieldFilter("status", "==", "pending"))
                       .on_snapshot(self._on_snapshot))

    def stop(self) -> None:
        if self._watch:
            self._watch.unsubscribe()
        self._pool.shutdown(wait=False, cancel_futures=True)

    # Step 2 ---------------------------------------------------------------- #
    def _on_snapshot(self, col_snapshot, changes, read_time) -> None:
        for change in changes:
            if change.type.name != "ADDED":
                continue
            doc_id = change.document.id
            with self._lock:
                if doc_id in self._seen:
                    continue
                self._seen.add(doc_id)
                self._in_flight += 1
            self._pool.submit(self._process, doc_id, change.document.to_dict())

    # Steps 3-6 ------------------------------------------------------------- #
    def _process(self, doc_id: str, data: dict) -> None:
        try:
            raw = base64.b64decode(data["image"])      # FE sends bare base64 PNG
            traits = run_ai(raw)                       # 3. AI (mock)
            if data.get("emotion") in EMOTIONS:        # the player's own picks win
                traits["emotion"] = data["emotion"]
            try:
                traits["size"] = float(data["size"])
            except (KeyError, TypeError, ValueError):
                pass
            character = build_character(doc_id, traits)  # 4. char_sim
            image = self._clean_image(raw)
            self.meta[doc_id] = {"name": str(data.get("name", "?"))[:40],
                                 "title": str(data.get("title", "?"))[:40]}
            try:
                self.col.document(doc_id).delete()     # 5. remove from Firestore
            except Exception as e:                     # still spawn; a restart would re-add it
                self.last_error = f"delete {doc_id}: {e!r}"
            self.incoming.put((character, image))      # 6. into the game
            with self._lock:
                self.received += 1
        except Exception as e:
            self.last_error = f"{doc_id}: {e!r}"
        finally:
            with self._lock:
                self._in_flight -= 1

    def _clean_image(self, raw: bytes) -> bytes | None:
        from firestore_store import prepare_image      # validates it really is an image
        try:
            return prepare_image(raw)[0]
        except ValueError as e:                        # bad image: spawn with a placeholder
            self.last_error = f"image rejected: {e}"
            return None


# --------------------------------------------------------------------------- #
# Same interface, no Firestore: invents a drawing every `every` seconds
# --------------------------------------------------------------------------- #
class DemoPipeline:
    label = "demo mode"

    def __init__(self, every: float = 0.4, seed: int = 1) -> None:
        self.incoming: queue.Queue = queue.Queue()
        self.received = 0
        self.meta: dict = {}
        self.pending = 0
        self.last_error = None
        self.every, self.rng, self._n = every, random.Random(seed), 0
        self._stop_evt = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self) -> None:
        self._stop_evt.set()

    def _run(self) -> None:
        r = self.rng
        while not self._stop_evt.is_set():
            self._n += 1
            t = random_traits(r)
            self.meta[f"demo{self._n}"] = {"name": f"Demo{self._n}", "title": "Doodle"}
            self.incoming.put((build_character(f"demo{self._n}", t), None))
            self.received += 1
            self._stop_evt.wait(self.every)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--cred")
    args = ap.parse_args()
    pipe = DemoPipeline() if args.demo else SpawnPipeline(args.cred)
    pipe.start()
    print(f"[{pipe.label}] waiting for drawings (Ctrl+C to stop)")
    try:
        while True:
            ch, img = pipe.incoming.get()
            print(f"new character {ch.id}: {ch.emotion}, traits={ch.traits}, "
                  f"image={'yes' if img else 'placeholder'}")
    except KeyboardInterrupt:
        pipe.stop()


if __name__ == "__main__":
    main()