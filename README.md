# StormHack2026_WetSandwich
StormHack



# Doodle Arena

Players draw a character on a web page. The drawing shows up in a live pygame world, where up to 1000 characters walk around, fight, rest and eventually die of old age.

```
 Web form (app.js)            Firestore                 Game (viewGame.py)
 ─────────────────            ─────────                 ───────────────────
 name, title, emotion,  ──►   spawns/<id>   ──listen──►  1. read the doc
 size, drawing (base64)       status:pending             2. AI step (random traits for now)
                                                         3. build the Character
                                                         4. delete the doc
                                                         5. spawn it in the world
```

New drawings appear in the game within about a second of being submitted (hopefully)

## Files

| File | What it does |
|---|---|
| `Code\src\viewGame.py` | The pygame window. **This is the thing you run.** |
| `Code\src\firebase_listener.py` | Listens to Firestore, runs the AI step, builds characters, deletes the docs |
| `Code\src\char_sim.py` | The simulation: movement, fights, health, traits, aging |
| `Code\src\firestore_store.py` | Firebase init and image validation helpers |
| `scripte\seed_spawns.sh` | Writes fake characters to Firestore for testing |
| `requirements.txt` | Python dependencies |
| `docs\index.html`, `app.js`, `config.js` | The web frontend |

## Setup

You need Python 3.10 or newer.

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Firebase service account key

The game talks to Firestore with the Firebase Admin SDK, which needs a private key.

1. Open the Firebase console, then Project settings, then Service accounts.
2. Click **Generate new private key**.
3. Save the file as `secrets/serviceAccountKey.json`.

This file is a real secret. Never commit it, and add this to `.gitignore`:

```
secrets/
venv/
seed_preview/
```

The key must be for the **same Firebase project** as the one in `config.js`.

## Running

You need two things running: the game and the web page.

### Terminal 1: the game (the Backend, only 1 server need to be running at once)

```bash
source venv/bin/activate
cd Code/src
python viewGame.py --cred secrets/serviceAccountKey.json
```


The HUD in the top left should say **listening to Firestore**.

### Terminal 2: the web page (Hosted on https://cyl74.github.io/StormHack2026_WetSandwich/)

`app.js` uses ES modules, so the page must be served over http. Double-clicking `index.html` will not work.

```bash
python -m http.server 8000
```

Open http://localhost:8000, draw or upload an image, fill in the form and click **Create character**. It should appear in the game almost immediately.

### Try it without Firestore

```bash
python viewGame.py --demo
```
This spawns a random character every 0.4 seconds and needs no Firebase at all.


## Seeding test data

`seed_spawns.sh` writes fake characters to `spawns` using the public API key from `config.js`. It needs `curl` and `python3` and nothing else.

```bash
chmod +x seed_spawns.sh
./seed_spawns.sh                 # 12 characters, 1 second apart
./seed_spawns.sh 50 0.2          # 50 characters, 0.2 seconds apart
DRY_RUN=1 ./seed_spawns.sh 5     # send nothing; save the request bodies to ./seed_preview/
```

Run it while the game is open and the characters trickle in one by one. On Windows, use Git Bash or WSL.

To target another project: `FIREBASE_API_KEY=... FIREBASE_PROJECT_ID=... ./seed_spawns.sh`.

## Tuning the game

| What | Where |
|---|---|
| How fast everyone moves | `SPEED_MULT` at the top of `char_sim.py` (default 3.0) |
| How hard every hit lands | `DAMAGE_MULT` at the top of `char_sim.py` (default 3.0) |
| How traits are chosen | `run_ai()` in `firebase_listener.py` |

### The AI step

`run_ai()` first tries `ai_traits()`, which is where the real AI (for example Gemini) goes. It isn't connected yet, so it always raises and falls back to `random_traits()`. That fallback gives each character 5 random traits plus random passivity, laziness, speed, damage and health.

The player's **emotion** and **size** from the web form always override whatever the AI or fallback returns. To wire in a real AI, fill in `ai_traits()` and return the same dict shape as `random_traits()`. The fallback keeps covering you if the API call fails.

## Firestore data

The web form adds one doc per character to the `spawns` collection:

```
name       string    "Bob"
title      string    "Builder"
emotion    string    happiness | sadness | fear | anger | anxiety
size       number    0.5 | 1 | 1.5 | 2
image      string    base64 PNG, 96x96, no "data:" prefix
status     string    "pending"
createdAt  timestamp server time
```

The game deletes each doc after it has taken the character. Docs left over from while the game was off are picked up when it starts. Docs that aren't `pending` are ignored.
