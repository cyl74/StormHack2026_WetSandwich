"""
Character simulation: traits -> stat modifiers -> movement parameters -> 2D update loop.

Pipeline:
    user input (emotion, size, passivity, laziness) + AI output (base stats, traits)
        -> effective_stats()   (trait modifiers, clamped)
        -> derive_params()     (one MovementParams bundle per character)
        -> World.step()        (shared update loop, spatial hash, attacks, aging)

Stdlib only. Run `python character_sim.py` for a headless demo.
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from collections import deque
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
WORLD_W, WORLD_H = 4000.0, 2000.0
MAX_CHARACTERS = 1000
MAX_TRAITS = 5
HP_DECAY_PER_SEC = 0.5          # 5 damage per 10 sec
SPEED_MULT = 200.0                # global knob: how much faster everyone moves
DAMAGE_MULT = 20.0               # global knob: how much harder every hit lands
BASE_SPEED_UNITS = 40.0 * SPEED_MULT   # world units/sec at speed stat 5 (x1.0)
BASE_RADIUS = 8.0
BASE_PERSONAL_SPACE = 40.0
MAX_PERSONAL_SPACE = 60.0
MAX_SENSE_RADIUS = 100.0        # also the spatial-hash cell size
LIMP_HP_FRACTION = 0.30
STAT_CAPS = {"speed": 999, "damage": 999, "health": 999}   # max total trait modifier (+/-) NO CAP
EMOTIONS = {"happiness", "sadness", "fear", "anger", "anxiety"}


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --------------------------------------------------------------------------- #
# Traits: single source of truth (share this with the AI prompt / backend)
# --------------------------------------------------------------------------- #
# Style keys:  *_mult multiply, *_add add, lean adds (clamped -1..1), flags union.
STYLE_DEFAULTS = dict(
    speed_mult=1.0, turn_mult=1.0, jitter_add=0.0, freeze_add=0.0, amp_add=0.0,
    freq_mult=1.0, lean=0, scale_mult=1.0, space_mult=1.0, approach_add=0.0,
    avoid_add=0.0, rest_add=0.0, attack_mult=1.0,
)

# (trait names, (speed, damage, health), style dict, flags)
_TRAIT_GROUPS = [
    (("energetic", "restless", "impatient"), (+1, 0, -5),
     dict(speed_mult=1.15, turn_mult=0.6, jitter_add=0.10, freq_mult=1.4), ()),
    (("alert", "attentive"), (+1, 0, 0),
     dict(turn_mult=0.7, freeze_add=0.02), ()),
    (("calm", "relaxed", "carefree"), (0, -1, +10),
     dict(speed_mult=0.85, turn_mult=1.5, jitter_add=-0.03), ("flowing",)),
    (("confident", "dominant", "outgoing"), (0, +1, +5),
     dict(scale_mult=1.1, space_mult=1.3, amp_add=0.1), ()),
    (("serious", "strict", "disciplined"), (0, +1, +5),
     dict(speed_mult=0.95, jitter_add=-0.04), ("rigid",)),
    (("thoughtful",), (-1, +1, +5),
     dict(speed_mult=0.85, turn_mult=1.6), ()),
    (("methodical", "perfectionist"), (-1, +1, +5),
     dict(speed_mult=0.9, jitter_add=-0.04), ("patrol",)),
    (("obsessive",), (0, +1, -5),
     dict(jitter_add=-0.04), ("patrol",)),
    (("excitable", "theatrical", "expressive"), (+1, 0, -5),
     dict(amp_add=0.4, freq_mult=1.3), ()),
    (("cheerful", "playful", "optimistic"), (+1, -1, +5),
     dict(amp_add=0.3, freq_mult=1.3, approach_add=0.2), ()),
    (("extroverted",), (0, -1, +5),
     dict(approach_add=0.3, amp_add=0.1), ()),
    (("empathetic", "connected"), (0, -1, +5),
     dict(approach_add=0.1), ("mirror",)),
    (("nervous", "anxious", "insecure"), (+1, -1, -10),
     dict(jitter_add=0.25, turn_mult=0.5, freeze_add=0.03, freq_mult=1.5), ()),
    (("shy", "reserved", "cautious"), (0, -1, 0),
     dict(avoid_add=0.3, amp_add=-0.05, scale_mult=0.95), ()),
    (("fearful", "submissive", "intimidated"), (+1, -2, -5),
     dict(avoid_add=0.5, scale_mult=0.85), ()),
    (("tired", "discouraged", "reluctant"), (-1, 0, -10),
     dict(speed_mult=0.8, rest_add=0.08, amp_add=-0.05), ()),
    (("determined", "ambitious", "focused"), (+1, +1, 0),
     dict(speed_mult=1.1, turn_mult=1.5, jitter_add=-0.03), ()),
    (("aggressive",), (+1, +1, -5),
     dict(lean=1, approach_add=0.4, attack_mult=1.5), ()),
    (("eager",), (+1, +1, -5),
     dict(lean=1, approach_add=0.3), ()),
    (("distracted", "indecisive", "detached"), (-1, 0, 0),
     dict(turn_mult=0.4, jitter_add=0.15), ()),
    (("chaotic", "impulsive", "erratic"), (+1, +1, -10),
     dict(jitter_add=0.5, turn_mult=0.4), ("erratic",)),
    (("skeptical", "guarded", "secretive"), (0, 0, +5),
     dict(lean=-1, avoid_add=0.1), ()),
    (("shocked", "startled"), (+1, -1, 0),
     dict(freeze_add=0.08), ()),
    (("curious", "interested"), (+1, 0, 0),
     dict(lean=1, approach_add=0.2), ()),
]


@dataclass(frozen=True)
class TraitDef:
    name: str
    speed: int
    damage: int
    health: int
    style: dict
    flags: tuple


TRAITS: dict[str, TraitDef] = {
    name: TraitDef(name, *mods, style, flags)
    for names, mods, style, flags in _TRAIT_GROUPS
    for name in names
}


def valid_traits(names) -> list[str]:
    """Filter/dedupe AI output down to known traits, capped at MAX_TRAITS."""
    out: list[str] = []
    for n in names:
        n = str(n).lower().strip()
        if n in TRAITS and n not in out:
            out.append(n)
    return out[:MAX_TRAITS]


# --------------------------------------------------------------------------- #
# Stats
# --------------------------------------------------------------------------- #
@dataclass
class Stats:
    speed: float    # 1-10
    damage: float   # 1-10
    health: float   # 1-100


def effective_stats(base: Stats, traits: list[str]) -> Stats:
    s = d = h = 0
    for t in traits:
        td = TRAITS.get(t)
        if td:
            s, d, h = s + td.speed, d + td.damage, h + td.health
    return Stats(
        speed=clamp(base.speed + clamp(s, -STAT_CAPS["speed"], STAT_CAPS["speed"]), 1, 10),
        damage=clamp(base.damage + clamp(d, -STAT_CAPS["damage"], STAT_CAPS["damage"]), 1, 10),
        health=clamp(base.health + clamp(h, -STAT_CAPS["health"], STAT_CAPS["health"]), 1, 100),
    )


# --------------------------------------------------------------------------- #
# Movement parameters
# --------------------------------------------------------------------------- #
@dataclass
class MovementParams:
    max_speed: float
    rest_prob: float          # per second
    rest_duration: float      # seconds
    turn_interval: float      # seconds between wander redirects
    jitter: float             # radians of heading noise per 0.1s
    personal_space: float
    sense_radius: float
    approach_weight: float
    avoid_weight: float
    attack_prob: float        # per second while a target is in reach
    freeze_prob: float        # per second
    anxiety_boost: float      # speed multiplier when someone is in personal space
    gesture_amp: float        # client-side animation hints
    gesture_freq: float
    lean: int
    scale: float
    radius: float
    flags: frozenset = frozenset()


def derive_params(emotion: str, size: float, passivity: float, laziness: float,
                  stats: Stats, traits: list[str]) -> MovementParams:
    # 1. accumulate trait styles
    st = dict(STYLE_DEFAULTS)
    flags: set[str] = set()
    for t in traits:
        td = TRAITS.get(t)
        if not td:
            continue
        for k, v in td.style.items():
            if k.endswith("_mult"):
                st[k] *= v
            else:
                st[k] += v
        flags.update(td.flags)
    st["lean"] = int(clamp(st["lean"], -1, 1))

    # 2. baseline from stats + user-set character/size
    speed = BASE_SPEED_UNITS * (0.5 + stats.speed / 10) * st["speed_mult"]
    speed /= (0.9 + 0.1 * size)                 # big characters are slightly slower
    speed *= 1 - 0.3 * laziness
    rest_prob = 0.02 + 0.30 * laziness + st["rest_add"]
    rest_duration = 1.0 + 4.0 * laziness
    turn_interval = 3.0 * st["turn_mult"]
    jitter = max(0.0, 0.05 + st["jitter_add"])
    space = min(MAX_PERSONAL_SPACE, BASE_PERSONAL_SPACE * size * st["space_mult"])
    approach = st["approach_add"]
    avoid = 0.3 * passivity + st["avoid_add"]
    attack = 0.05 * st["attack_mult"] * (1 - 0.8 * passivity)
    freeze = 0.01 + st["freeze_add"]
    amp = 0.1 + st["amp_add"]
    freq = st["freq_mult"]
    scale = size * st["scale_mult"] * 10
    lean = st["lean"]
    boost = 1.0

    # 3. emotion overlay (user-set, applied last)
    if emotion == "happiness":
        attack = 0.0
        approach += 0.5
        amp += 0.3
        freq *= 1.2
    elif emotion == "sadness":
        speed *= 0.6
        rest_prob += 0.10
        amp = max(0.02, amp - 0.05)
        freq *= 0.7
        lean = 0
    elif emotion == "fear":
        avoid += 1.0
        space = min(MAX_PERSONAL_SPACE * 1.5, space * 1.5)
        scale *= 0.85
        freeze += 0.04
        attack *= 0.3
    elif emotion == "anger":
        attack = 0.5 * (1 - 0.8 * passivity) * st["attack_mult"]
        approach += 0.8
        speed *= 1.1
        lean = 1
    elif emotion == "anxiety":
        jitter += 0.20
        turn_interval *= 0.5
        boost = 1.5
        freq *= 1.5

    return MovementParams(
        max_speed=speed, rest_prob=rest_prob, rest_duration=rest_duration,
        turn_interval=max(0.3, turn_interval), jitter=jitter, personal_space=space,
        sense_radius=min(MAX_SENSE_RADIUS, space * 2), approach_weight=approach,
        avoid_weight=avoid, attack_prob=attack, freeze_prob=freeze, anxiety_boost=boost,
        gesture_amp=amp, gesture_freq=freq, lean=lean, scale=scale,
        radius=BASE_RADIUS * scale, flags=frozenset(flags),
    )


# --------------------------------------------------------------------------- #
# Character
# --------------------------------------------------------------------------- #
@dataclass
class Character:
    id: int
    image: str                    # reference to the drawing (path/URL/blob id)
    emotion: str
    size: float                   # ~0.5 - 2.0
    passivity: float              # 0-1
    laziness: float               # 0-1
    base: Stats
    traits: list[str]
    x: float = 0.0
    y: float = 0.0
    heading: float = 0.0
    facing: float = 0.0
    age: float = 0.0
    max_age: float = 300.0
    state: str = "walk"           # walk | rest | freeze
    state_timer: float = 0.0
    turn_timer: float = 0.0
    speed_burst: float = 1.0
    stats: Stats = field(init=False)
    params: MovementParams = field(init=False)
    hp: float = field(init=False)
    max_hp: float = field(init=False)

    def __post_init__(self):
        self.emotion = self.emotion if self.emotion in EMOTIONS else "happiness"
        self.traits = valid_traits(self.traits)
        self.refresh()
        self.hp = self.max_hp

    def refresh(self) -> None:
        self.stats = effective_stats(self.base, self.traits)
        self.params = derive_params(self.emotion, self.size, self.passivity,
                                    self.laziness, self.stats, self.traits)
        self.max_hp = self.stats.health

    def set_traits(self, traits) -> None:
        """AI trait drift. Applies the max-HP *difference* so it never heals."""
        old_max = self.max_hp
        self.traits = valid_traits(traits)
        self.refresh()
        self.hp = max(1.0, min(self.max_hp, self.hp + (self.max_hp - old_max)))

    def set_emotion(self, emotion: str) -> None:
        if emotion in EMOTIONS:
            self.emotion = emotion
            self.refresh()

    def to_public(self) -> dict:
        """What clients need to render. Animation runs client-side from these hints."""
        p = self.params
        return dict(
            id=self.id, image=self.image, x=round(self.x, 1), y=round(self.y, 1),
            facing=round(self.facing, 2), state=self.state, hp=round(self.hp, 1),
            max_hp=round(self.max_hp, 1), emotion=self.emotion, scale=round(p.scale, 2),
            amp=round(p.gesture_amp, 2), freq=round(p.gesture_freq, 2), lean=p.lean,
            limp=self.hp < LIMP_HP_FRACTION * self.max_hp,
        )


# --------------------------------------------------------------------------- #
# Spatial hash
# --------------------------------------------------------------------------- #
class SpatialHash:
    def __init__(self, cell: float = MAX_SENSE_RADIUS):
        self.cell = cell
        self.cells: dict[tuple[int, int], list[Character]] = {}

    def rebuild(self, chars) -> None:
        self.cells.clear()
        c = self.cell
        for ch in chars:
            self.cells.setdefault((int(ch.x // c), int(ch.y // c)), []).append(ch)

    def query(self, x: float, y: float, r: float, exclude: Character):
        c = self.cell
        cx, cy = int(x // c), int(y // c)
        span = int(r // c) + 1
        r2 = r * r
        out = []
        for gx in range(cx - span, cx + span + 1):
            for gy in range(cy - span, cy + span + 1):
                for o in self.cells.get((gx, gy), ()):
                    if o is exclude:
                        continue
                    dx, dy = o.x - x, o.y - y
                    d2 = dx * dx + dy * dy
                    if d2 <= r2:
                        out.append((math.sqrt(d2), o))
        out.sort(key=lambda t: t[0])
        return out


# --------------------------------------------------------------------------- #
# World
# --------------------------------------------------------------------------- #
class World:
    def __init__(self, max_characters: int = MAX_CHARACTERS, seed: int | None = None):
        self.chars: dict[int, Character] = {}
        self.max_characters = max_characters
        self.grid = SpatialHash()
        self.time = 0.0
        # ("attack", attacker_id, victim_id, dmg) / ("despawn", id, reason) / ("spawn", id)
        # Consumed by the AI trait-drift job and by broadcast code.
        self.events: deque = deque(maxlen=10_000)
        self.rng = random.Random(seed)

    # -- lifecycle ---------------------------------------------------------- #
    @property
    def free_slots(self) -> int:
        return self.max_characters - len(self.chars)

    def spawn(self, ch: Character) -> bool:
        if self.free_slots <= 0:
            return False
        ch.x = self.rng.uniform(0, WORLD_W)
        ch.y = self.rng.uniform(0, WORLD_H)
        ch.heading = ch.facing = self.rng.uniform(0, math.tau)
        ch.turn_timer = ch.params.turn_interval * self.rng.uniform(0.5, 1.5)
        ch.max_age = self.rng.uniform(240, 360)
        self.chars[ch.id] = ch
        self.events.append(("spawn", ch.id))
        return True

    def _despawn(self, ch: Character, reason: str) -> None:
        del self.chars[ch.id]
        self.events.append(("despawn", ch.id, reason))

    # -- simulation --------------------------------------------------------- #
    def step(self, dt: float) -> None:
        self.time += dt
        self.grid.rebuild(self.chars.values())
        pending: dict[int, float] = {}
        for ch in self.chars.values():
            self._update(ch, dt, pending)

        for cid, dmg in pending.items():
            if cid in self.chars:
                self.chars[cid].hp -= dmg
        for ch in list(self.chars.values()):
            if ch.hp <= 0:
                self._despawn(ch, "dead")
            elif ch.age >= ch.max_age:
                self._despawn(ch, "old_age")

    def _update(self, ch: Character, dt: float, pending: dict[int, float]) -> None:
        p, rng = ch.params, self.rng
        ch.age += dt
        ch.hp -= HP_DECAY_PER_SEC * dt

        # resting / frozen: stand still until the timer ends
        if ch.state_timer > 0:
            ch.state_timer -= dt
            if ch.state_timer <= 0:
                ch.state = "walk"
            return
        if rng.random() < p.rest_prob * dt:
            ch.state, ch.state_timer = "rest", p.rest_duration * rng.uniform(0.6, 1.4)
            return
        if rng.random() < p.freeze_prob * dt:
            ch.state, ch.state_timer = "freeze", rng.uniform(0.3, 1.0)
            return

        # wander heading
        ch.turn_timer -= dt
        if ch.turn_timer <= 0:
            if "patrol" in p.flags:
                ch.heading += math.pi
            elif "rigid" in p.flags:
                ch.heading = rng.choice((0, 1, 2, 3)) * math.pi / 2
            elif "flowing" in p.flags:
                ch.heading += rng.gauss(0, 0.4)
            else:
                ch.heading = rng.uniform(0, math.tau)
            ch.speed_burst = rng.uniform(0.5, 1.6) if "erratic" in p.flags else 1.0
            ch.turn_timer = p.turn_interval * rng.uniform(0.7, 1.3)
        if "rigid" not in p.flags and "patrol" not in p.flags:
            ch.heading += rng.gauss(0, p.jitter) * math.sqrt(dt / 0.1)

        dx, dy = math.cos(ch.heading), math.sin(ch.heading)

        # social steering
        neighbors = self.grid.query(ch.x, ch.y, p.sense_radius, ch)
        crowded = False
        if neighbors:
            d0, nearest = neighbors[0]
            if d0 > 1e-6:
                ux, uy = (nearest.x - ch.x) / d0, (nearest.y - ch.y) / d0
                dx += p.approach_weight * ux
                dy += p.approach_weight * uy
                if "mirror" in p.flags:
                    dx += 0.5 * math.cos(nearest.heading)
                    dy += 0.5 * math.sin(nearest.heading)
            for d, n in neighbors:
                if d >= p.personal_space:
                    break
                crowded = True
                if d > 1e-6 and p.avoid_weight:
                    k = p.avoid_weight * (1 - d / p.personal_space)
                    dx -= k * (n.x - ch.x) / d
                    dy -= k * (n.y - ch.y) / d

            # attack the nearest character in reach (happy characters never attack)
            reach = p.radius + nearest.params.radius + 4
            if d0 <= reach and p.attack_prob > 0 and rng.random() < p.attack_prob * dt:
                dmg = ch.stats.damage * DAMAGE_MULT
                pending[nearest.id] = pending.get(nearest.id, 0.0) + dmg
                self.events.append(("attack", ch.id, nearest.id, dmg))

        norm = math.hypot(dx, dy)
        if norm > 1e-6:
            dx, dy = dx / norm, dy / norm
            ch.facing = math.atan2(dy, dx)

        # speed modifiers
        spd = p.max_speed * ch.speed_burst
        if crowded:
            spd *= p.anxiety_boost
        if ch.hp < LIMP_HP_FRACTION * ch.max_hp:
            spd *= 0.7

        ch.x += dx * spd * dt
        ch.y += dy * spd * dt
        if not 0 <= ch.x <= WORLD_W:
            ch.x = clamp(ch.x, 0, WORLD_W)
            ch.heading = math.pi - ch.heading
        if not 0 <= ch.y <= WORLD_H:
            ch.y = clamp(ch.y, 0, WORLD_H)
            ch.heading = -ch.heading

    def snapshot(self) -> list[dict]:
        """Payload for the WebSocket broadcast (~10 Hz)."""
        return [c.to_public() for c in self.chars.values()]

    def pop_events(self) -> list[tuple]:
        evs = list(self.events)
        self.events.clear()
        return evs


# --------------------------------------------------------------------------- #
# SQL spawn queue (replaces "every 15 sec read and delete")
# --------------------------------------------------------------------------- #
class SpawnQueue:
    """The AI backend INSERTs rows; the sim claims them. Rows are marked spawned
    (not deleted immediately) and purged later, so a crash can't lose characters."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS spawn_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image TEXT NOT NULL,
        emotion TEXT NOT NULL,
        size REAL NOT NULL,
        passivity REAL NOT NULL DEFAULT 0.5,
        laziness REAL NOT NULL DEFAULT 0.3,
        base_health REAL NOT NULL,
        base_speed REAL NOT NULL,
        base_damage REAL NOT NULL,
        traits TEXT NOT NULL,                 -- JSON array of trait names
        spawned INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_spawn_pending ON spawn_queue (spawned, id);
    """

    def __init__(self, path: str = ":memory:"):
        self.db = sqlite3.connect(path)
        self.db.executescript(self.SCHEMA)

    def enqueue(self, *, image, emotion, size, base: Stats, traits,
                passivity=0.5, laziness=0.3) -> int:
        with self.db:
            cur = self.db.execute(
                "INSERT INTO spawn_queue (image, emotion, size, passivity, laziness,"
                " base_health, base_speed, base_damage, traits, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (image, emotion, clamp(size, 0.5, 2.0), clamp(passivity, 0, 1),
                 clamp(laziness, 0, 1), base.health, base.speed, base.damage,
                 json.dumps(valid_traits(traits)), time.time()),
            )
        return cur.lastrowid

    def claim(self, limit: int) -> list[Character]:
        if limit <= 0:
            return []
        with self.db:
            rows = self.db.execute(
                "SELECT id, image, emotion, size, passivity, laziness, base_health,"
                " base_speed, base_damage, traits FROM spawn_queue"
                " WHERE spawned = 0 ORDER BY id LIMIT ?", (limit,)).fetchall()
            if rows:
                self.db.executemany("UPDATE spawn_queue SET spawned = 1 WHERE id = ?",
                                    [(r[0],) for r in rows])
        return [
            Character(id=r[0], image=r[1], emotion=r[2], size=r[3], passivity=r[4],
                      laziness=r[5], base=Stats(speed=r[7], damage=r[8], health=r[6]),
                      traits=json.loads(r[9]))
            for r in rows
        ]

    def purge_spawned(self) -> int:
        with self.db:
            return self.db.execute("DELETE FROM spawn_queue WHERE spawned = 1").rowcount

    def pending(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM spawn_queue WHERE spawned = 0").fetchone()[0]


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def poll_spawns(world: World, queue: SpawnQueue) -> int:
    """Claim only as many rows as there are free slots, so the 1000 cap gives
    natural backpressure and the rest stay queued."""
    n = 0
    for ch in queue.claim(world.free_slots):
        n += world.spawn(ch)
    return n


def run(world: World, queue: SpawnQueue, seconds: float, tick_hz: int = 10,
        poll_every: float = 15.0, realtime: bool = False, on_poll=None) -> None:
    dt = 1.0 / tick_hz
    next_poll = 0.0
    for _ in range(int(seconds * tick_hz)):
        t0 = time.perf_counter()
        if world.time >= next_poll:
            poll_spawns(world, queue)
            queue.purge_spawned()
            next_poll += poll_every
            if on_poll:
                on_poll(world, queue)
        world.step(dt)
        # world.snapshot() -> broadcast over WebSocket here
        if realtime:
            time.sleep(max(0.0, dt - (time.perf_counter() - t0)))


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
def _demo() -> None:
    rng = random.Random(7)
    trait_pool = list(TRAITS)
    queue = SpawnQueue()
    for i in range(1200):  # more than the cap, to show backpressure
        queue.enqueue(
            image=f"drawing_{i}.png",
            emotion=rng.choice(sorted(EMOTIONS)),
            size=rng.uniform(0.6, 1.6),
            passivity=rng.random(), laziness=rng.random(),
            base=Stats(speed=rng.randint(1, 10), damage=rng.randint(1, 10),
                       health=rng.randint(20, 100)),
            traits=rng.sample(trait_pool, rng.randint(1, 4)),
        )

    world = World(seed=1)
    stats = {"attack": 0, "dead": 0, "old_age": 0}

    def report(w: World, q: SpawnQueue) -> None:
        for ev in w.pop_events():
            if ev[0] == "attack":
                stats["attack"] += 1
            elif ev[0] == "despawn":
                stats[ev[2]] += 1
        print(f"t={w.time:5.0f}s  alive={len(w.chars):4d}  queued={q.pending():4d}  "
              f"attacks={stats['attack']:4d}  deaths={stats['dead']:3d}  "
              f"old_age={stats['old_age']:3d}")

    t0 = time.perf_counter()
    run(world, queue, seconds=60, on_poll=report)
    print(f"\n60 simulated seconds in {time.perf_counter() - t0:.1f}s real time")

    # AI trait drift example
    c = next(iter(world.chars.values()))
    before = (c.traits[:], round(c.hp, 1), round(c.max_hp, 1))
    c.set_traits(c.traits + ["aggressive"])
    print("trait drift:", before, "->", (c.traits, round(c.hp, 1), round(c.max_hp, 1)))


if __name__ == "__main__":
    _demo()