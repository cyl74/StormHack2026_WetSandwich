"""
Pygame viewer for the character simulation.

A SpawnPipeline (firebase_listener.py) listens to Firestore: when the frontend writes a
drawing it runs the AI step, builds the Character, deletes the Firestore docs and queues
the character here.
The main thread owns the World: it steps the sim at a fixed 10 Hz, interpolates
positions for smooth 60 fps rendering, and spawns whatever the poller delivered.

Run:
    python game_view.py --demo                 # no Firestore, random doodles
    python game_view.py                        # Firestore (GOOGLE_APPLICATION_CREDENTIALS)
    python game_view.py --cred secrets/serviceAccountKey.json

Controls:
    WASD / arrows or left-drag   pan            mouse wheel   zoom
    F  fit whole world           SPACE pause    T  speed 1x/2x/4x
    click a character to inspect it             H  toggle HUD      ESC quit
"""
from __future__ import annotations

import argparse
import io
import math
import queue
import random
import time
from collections import Counter

import pygame

from char_sim import LIMP_HP_FRACTION, WORLD_H, WORLD_W, World
from firebase_listener import DemoPipeline, SpawnPipeline

TICK_HZ = 10
DT = 1.0 / TICK_HZ
SPRITE_UNITS = 40.0          # sprite height in world units at scale 1.0
MAX_SPAWNS_PER_FRAME = 25    # decode budget so a big batch doesn't freeze the window

# BG = (24, 26, 33)
BG = (245, 242, 235)
GRID = (36, 39, 48)
EMOTION_COLORS = {
    "happiness": (255, 205, 60),
    "sadness": (90, 140, 255),
    "fear": (170, 110, 230),
    "anger": (240, 70, 60),
    "anxiety": (255, 150, 50),
}


# --------------------------------------------------------------------------- #
# Sprites
# --------------------------------------------------------------------------- #
def make_placeholder(cid, emotion: str) -> pygame.Surface:
    rng = random.Random(str(cid))
    s = pygame.Surface((64, 64), pygame.SRCALPHA)
    col = pygame.Color(0)
    col.hsva = (rng.randrange(360), 55, 95, 100)
    pygame.draw.circle(s, col, (32, 32), 28)
    pygame.draw.circle(s, (30, 30, 30), (32, 32), 28, 3)
    for ex in (22, 42):
        pygame.draw.circle(s, (255, 255, 255), (ex, 26), 6)
        pygame.draw.circle(s, (20, 20, 20), (ex + rng.randint(-1, 1), 27), 3)
    if emotion in ("happiness",):
        pygame.draw.arc(s, (30, 30, 30), (20, 30, 24, 18), math.pi, math.tau, 3)
    elif emotion in ("sadness", "fear"):
        pygame.draw.arc(s, (30, 30, 30), (20, 42, 24, 14), 0, math.pi, 3)
    else:
        pygame.draw.line(s, (30, 30, 30), (22, 46), (42, 46), 3)
    return s


class SpriteBank:
    def __init__(self) -> None:
        self.base: dict = {}
        self.scaled: dict = {}      # id -> {(px, flip): Surface}

    def add(self, cid, data: bytes | None, emotion: str) -> None:
        surf = None
        if data:
            try:
                surf = pygame.image.load(io.BytesIO(data)).convert_alpha()
            except Exception:
                surf = None
        self.base[cid] = surf or make_placeholder(cid, emotion)

    def remove(self, cid) -> None:
        self.base.pop(cid, None)
        self.scaled.pop(cid, None)

    def get(self, cid, px: int, flip: bool) -> pygame.Surface:
        per = self.scaled.setdefault(cid, {})
        s = per.get((px, flip))
        if s is None:
            if len(per) > 8:                                # zoomed a lot: drop stale sizes
                per.clear()
            base = self.base[cid]
            w, h = base.get_size()
            k = px / max(w, h)
            s = pygame.transform.smoothscale(base, (max(1, int(w * k)), max(1, int(h * k))))
            if flip:
                s = pygame.transform.flip(s, True, False)
            per[(px, flip)] = s
        return s


# --------------------------------------------------------------------------- #
# Camera
# --------------------------------------------------------------------------- #
class Camera:
    def __init__(self) -> None:
        self.x, self.y, self.zoom = WORLD_W / 2, WORLD_H / 2, 0.4

    def fit(self, w: int, h: int) -> None:
        self.x, self.y = WORLD_W / 2, WORLD_H / 2
        self.zoom = min(w / WORLD_W, h / WORLD_H) * 0.95

    def to_screen(self, wx, wy, w, h):
        return (wx - self.x) * self.zoom + w / 2, (wy - self.y) * self.zoom + h / 2

    def to_world(self, sx, sy, w, h):
        return (sx - w / 2) / self.zoom + self.x, (sy - h / 2) / self.zoom + self.y

    def zoom_at(self, mx, my, w, h, factor) -> None:
        wx, wy = self.to_world(mx, my, w, h)
        self.zoom = max(0.12, min(5.0, self.zoom * factor))
        self.x, self.y = wx - (mx - w / 2) / self.zoom, wy - (my - h / 2) / self.zoom

    def clamp(self) -> None:
        self.x = max(0, min(WORLD_W, self.x))
        self.y = max(0, min(WORLD_H, self.y))


# --------------------------------------------------------------------------- #
# Drawing
# --------------------------------------------------------------------------- #
def draw_grid(screen, cam: Camera) -> None:
    w, h = screen.get_size()
    step = 200
    for i in range(0, int(WORLD_W) + 1, step):
        x0, y0 = cam.to_screen(i, 0, w, h)
        x1, y1 = cam.to_screen(i, WORLD_H, w, h)
        pygame.draw.line(screen, GRID, (x0, y0), (x1, y1))
        x0, y0 = cam.to_screen(0, i, w, h)
        x1, y1 = cam.to_screen(WORLD_W, i, w, h)
        pygame.draw.line(screen, GRID, (x0, y0), (x1, y1))
    tl = cam.to_screen(0, 0, w, h)
    br = cam.to_screen(WORLD_W, WORLD_H, w, h)
    pygame.draw.rect(screen, (90, 95, 110), (*tl, br[0] - tl[0], br[1] - tl[1]), 2)


def draw_characters(screen, world: World, prev: dict, alpha: float, cam: Camera,
                    bank: SpriteBank, now: float, selected) -> None:
    w, h = screen.get_size()
    z = cam.zoom
    visible = []
    for ch in world.chars.values():
        ox, oy = prev.get(ch.id, (ch.x, ch.y))
        x, y = ox + (ch.x - ox) * alpha, oy + (ch.y - oy) * alpha
        grow = min(1.0, ch.age / 0.5)                       # pop-in on spawn
        size_px = ch.params.scale * SPRITE_UNITS * z * grow
        sx, sy = cam.to_screen(x, y, w, h)
        if -size_px < sx < w + size_px and -size_px < sy < h + size_px:
            visible.append((sy, sx, size_px, ch))
    visible.sort(key=lambda t: t[0])                        # lower on screen = in front

    for sy, sx, size_px, ch in visible:
        col = EMOTION_COLORS.get(ch.emotion, (200, 200, 200))
        if size_px < 7:                                     # zoomed way out: just dots
            pygame.draw.circle(screen, col, (int(sx), int(sy)), max(1, int(size_px / 2)))
            continue

        p = ch.params
        phase = (hash(ch.id) % 628) / 100.0
        moving = ch.state == "walk"
        speed_k = 0.6 if ch.hp < LIMP_HP_FRACTION * ch.max_hp else 1.0
        wave = math.sin(now * p.gesture_freq * 6 * speed_k + phase)
        hop = abs(wave) * p.gesture_amp * size_px * 0.5 if moving else 0.0
        angle = wave * p.gesture_amp * 20 if moving else 0.0
        angle -= p.lean * 8 * (1 if math.cos(ch.facing) >= 0 else -1)
        if ch.hp < LIMP_HP_FRACTION * ch.max_hp:
            angle += 10
        px = int(size_px // 4 * 4) or 4                      # bucket sizes for the cache
        if ch.state == "rest":
            px = int(px * (1 + 0.03 * math.sin(now * 2 + phase)))

        flip = math.cos(ch.facing) < 0
        spr = bank.get(ch.id, px, flip)
        if abs(angle) > 1 and px >= 14:
            spr = pygame.transform.rotate(spr, -angle)

        # shadow ring in the emotion colour, then sprite
        ring = pygame.Rect(0, 0, px * 0.8, px * 0.28)
        ring.center = (sx, sy + px * 0.05)
        pygame.draw.ellipse(screen, col, ring, 2)
        screen.blit(spr, spr.get_rect(midbottom=(sx, sy - hop)))

        if ch.hp < ch.max_hp * 0.99 and px >= 20:           # health bar once damaged
            bw = px * 0.7
            frac = max(0.0, ch.hp / ch.max_hp)
            bar = pygame.Rect(0, 0, bw, 4)
            bar.midtop = (sx, sy + px * 0.15)
            pygame.draw.rect(screen, (60, 20, 20), bar)
            bar.width = int(bw * frac)
            pygame.draw.rect(screen, (90, 220, 110) if frac > LIMP_HP_FRACTION else (230, 80, 70), bar)
        if ch.id == selected:
            pygame.draw.circle(screen, (255, 255, 255), (int(sx), int(sy - px / 2)),
                               int(px * 0.7), 2)


def draw_effects(screen, effects: list, cam: Camera, now: float) -> None:
    w, h = screen.get_size()
    for e in effects[:]:
        age = now - e["t"]
        if age > 0.4:
            effects.remove(e)
            continue
        k = age / 0.4
        a = cam.to_screen(e["x"], e["y"], w, h)
        if e["kind"] == "attack":
            b = cam.to_screen(e["x2"], e["y2"], w, h)
            pygame.draw.line(screen, (255, 90, 80), a, b, 2)
            pygame.draw.circle(screen, (255, 200, 120), b, int(4 + 10 * k), 2)
        else:                                               # despawn poof
            col = (255, 255, 255) if e["reason"] == "old_age" else (200, 60, 60)
            pygame.draw.circle(screen, col, a, int(8 + 26 * k * cam.zoom * 3), 2)


def draw_hud(screen, font, small, world, feeder, clock, speed, paused, counts, selected) -> None:
    lines = [
        f"alive {len(world.chars)}   queued {feeder.pending}   sim {world.time:5.0f}s   "
        f"speed {speed}x{'  PAUSED' if paused else ''}   {clock.get_fps():3.0f} fps",
        f"attacks {counts['attack']}   deaths {counts['dead']}",
        # f"attacks {counts['attack']}   deaths {counts['dead']}   old age {counts['old_age']}",
        f"{feeder.label}   received {feeder.received}",
    ]
    if feeder.last_error:
        lines.append(f"pipeline error: {feeder.last_error[:90]}")
    pad = pygame.Rect(8, 8, 520, 8 + 20 * len(lines))
    pygame.draw.rect(screen, (0, 0, 0, 150), pad, border_radius=6)
    for i, t in enumerate(lines):
        c = (255, 120, 110) if t.startswith("pipeline error") else (225, 228, 235)
        screen.blit(font.render(t, True, c), (16, 14 + 20 * i))

    ch = world.chars.get(selected)
    if ch:
        m = feeder.meta.get(ch.id)
        info = [f"{m['name']} the {m['title']}" if m else f"{ch.id}", f"emotion: {ch.emotion}   size {ch.size:.2f}",
                f"hp {ch.hp:.0f}/{ch.max_hp:.0f}   age {ch.age:.0f}/{ch.max_age:.0f}s",
                f"speed {ch.stats.speed:.0f}  damage {ch.stats.damage:.0f}   state {ch.state}",
                "traits: " + ", ".join(ch.traits)]
        box = pygame.Rect(8, screen.get_height() - 16 - 20 * len(info), 460, 12 + 20 * len(info))
        pygame.draw.rect(screen, (0, 0, 0), box, border_radius=6)
        for i, t in enumerate(info):
            screen.blit(small.render(t, True, (225, 228, 235)), (box.x + 8, box.y + 6 + 20 * i))


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="fake characters, no Firestore")
    ap.add_argument("--cred", help="path to serviceAccountKey.json")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--frames", type=int, help="quit after N frames (testing)")
    ap.add_argument("--shot", help="save a screenshot on exit (testing)")
    args = ap.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((1280, 800), pygame.RESIZABLE)
    pygame.display.set_caption("Character world")
    font, small = pygame.font.Font(None, 22), pygame.font.Font(None, 22)
    clock = pygame.time.Clock()

    world = World(seed=args.seed)
    feeder = DemoPipeline() if args.demo else SpawnPipeline(args.cred)
    feeder.start()
    bank, cam = SpriteBank(), Camera()
    cam.fit(*screen.get_size())

    prev: dict = {}
    effects: list = []
    counts: Counter = Counter()
    selected = None
    acc, speed, paused, show_hud = 0.0, 1, False, True
    press_pos, dragging = None, False
    frame = 0
    running = True

    while running:
        frame_dt = min(clock.tick(60) / 1000.0, 0.25)
        now = time.time()
        w, h = screen.get_size()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_SPACE:
                    paused = not paused
                elif ev.key == pygame.K_t:
                    speed = {1: 2, 2: 4, 4: 1}[speed]
                elif ev.key == pygame.K_f:
                    cam.fit(w, h)
                elif ev.key == pygame.K_h:
                    show_hud = not show_hud
            elif ev.type == pygame.MOUSEWHEEL:
                cam.zoom_at(*pygame.mouse.get_pos(), w, h, 1.15 ** ev.y)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                press_pos, dragging = ev.pos, False
            elif ev.type == pygame.MOUSEMOTION and ev.buttons[0] and press_pos:
                if dragging or math.dist(ev.pos, press_pos) > 4:
                    dragging = True
                    cam.x -= ev.rel[0] / cam.zoom
                    cam.y -= ev.rel[1] / cam.zoom
            elif ev.type == pygame.MOUSEBUTTONUP and ev.button == 1:
                if press_pos and not dragging:              # a click: pick nearest character
                    wx, wy = cam.to_world(*ev.pos, w, h)
                    reach = max(14 / cam.zoom, 12)
                    best = min(world.chars.values(), default=None,
                               key=lambda c: math.hypot(c.x - wx, c.y - wy))
                    selected = (best.id if best and math.hypot(best.x - wx, best.y - wy) < reach
                                else None)
                press_pos = None

        keys = pygame.key.get_pressed()
        pan = 600 / cam.zoom * frame_dt
        cam.x += pan * (keys[pygame.K_d] or keys[pygame.K_RIGHT]) - pan * (keys[pygame.K_a] or keys[pygame.K_LEFT])
        cam.y += pan * (keys[pygame.K_s] or keys[pygame.K_DOWN]) - pan * (keys[pygame.K_w] or keys[pygame.K_UP])
        cam.clamp()

        # characters delivered by the pipeline (budgeted so a big batch can't stall a frame)
        for _ in range(MAX_SPAWNS_PER_FRAME):
            if world.free_slots <= 0:
                break                                       # world full: arrivals wait in memory
            try:
                ch, data = feeder.incoming.get_nowait()
            except queue.Empty:
                break
            if world.spawn(ch):
                bank.add(ch.id, data, ch.emotion)

        # fixed-step simulation
        if not paused:
            acc += frame_dt * speed
        while acc >= DT:
            acc -= DT
            prev = {c.id: (c.x, c.y) for c in world.chars.values()}
            world.step(DT)
            for e in world.pop_events():
                if e[0] == "despawn":
                    cid, reason = e[1], e[2]
                    counts[reason] += 1
                    x, y = prev.get(cid, (0, 0))
                    effects.append(dict(kind="poof", x=x, y=y, reason=reason, t=now))
                    bank.remove(cid)
                    feeder.meta.pop(cid, None)
                    if selected == cid:
                        selected = None
                elif e[0] == "attack":
                    a, v = world.chars.get(e[1]), world.chars.get(e[2])
                    counts["attack"] += 1
                    if a and v and len(effects) < 300:
                        effects.append(dict(kind="attack", x=a.x, y=a.y, x2=v.x, y2=v.y, t=now))

        # render
        screen.fill(BG)
        draw_grid(screen, cam)
        draw_characters(screen, world, prev, acc / DT if not paused else 1.0, cam, bank, now, selected)
        draw_effects(screen, effects, cam, now)
        if show_hud:
            draw_hud(screen, font, small, world, feeder, clock, speed, paused, counts, selected)
        pygame.display.flip()

        frame += 1
        if args.frames and frame >= args.frames:
            running = False

    if args.shot:
        pygame.image.save(screen, args.shot)
    feeder.stop()
    pygame.quit()


if __name__ == "__main__":
    main()