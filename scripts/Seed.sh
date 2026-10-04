#!/usr/bin/env bash
# Seed fake characters into the `spawns` collection, exactly like the web form does,
# using the public web API key from config.js (via the Firestore REST API).
#
# Usage:
#   ./seed_spawns.sh              # 12 characters, 1 second apart
#   ./seed_spawns.sh 50 0.2       # 50 characters, 0.2 s apart
#   DRY_RUN=1 ./seed_spawns.sh 5  # write the request bodies to ./seed_preview/, send nothing
#
# Needs: bash, curl, python3 (standard library only).
set -euo pipefail

API_KEY="${FIREBASE_API_KEY:-AIzaSyAKgGYWKKidaG8le8AdbP4qGVTget-ZFPg}"
PROJECT_ID="${FIREBASE_PROJECT_ID:-wet-sandwich-d7ba3}"
COUNT="${1:-12}"
DELAY="${2:-1}"
DRY_RUN="${DRY_RUN:-0}"

for cmd in curl python3; do
  command -v "$cmd" >/dev/null || { echo "missing required command: $cmd" >&2; exit 1; }
done

if [ "$DRY_RUN" = "1" ]; then
  OUT="./seed_preview"; mkdir -p "$OUT"; rm -f "$OUT"/body_*.json
else
  OUT="$(mktemp -d)"; trap 'rm -rf "$OUT"' EXIT
fi

# Build one request body per character (random name/title/emotion/size + a drawn PNG sprite).
python3 - "$COUNT" "$OUT" "$PROJECT_ID" <<'PY'
import base64, colorsys, json, math, random, string, struct, sys, zlib

count, out, project = int(sys.argv[1]), sys.argv[2], sys.argv[3]
EMOTIONS = ["happiness", "sadness", "fear", "anger", "anxiety"]
SIZES = [0.5, 1, 1.5, 2]
NAMES = ["Bob", "Alice", "Zed", "Mina", "Otto", "Priya", "Juno", "Kai", "Lola", "Moe",
         "Nia", "Rex", "Suki", "Tobias", "Uma", "Vik", "Wren", "Yara"]
TITLES = ["Builder", "Baker", "Wizard", "Knight", "Farmer", "Pirate", "Poet", "Thief",
          "Cook", "Bard", "Monk", "Hunter", "Sailor", "Scholar"]
S = 96

def png(pix):
    raw = b"".join(b"\x00" + bytes(pix[y * S * 4:(y + 1) * S * 4]) for y in range(S))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))

def sprite(emotion):
    pix = bytearray(S * S * 4)                      # transparent background
    def dot(x, y, r, c):
        for yy in range(int(y - r), int(y + r) + 1):
            for xx in range(int(x - r), int(x + r) + 1):
                if 0 <= xx < S and 0 <= yy < S and (xx - x) ** 2 + (yy - y) ** 2 <= r * r:
                    i = (yy * S + xx) * 4
                    pix[i:i + 4] = bytes(c)
    h = random.random()
    body = [int(v * 255) for v in colorsys.hsv_to_rgb(h, 0.55, 0.95)] + [255]
    ink, white = (30, 30, 30, 255), (255, 255, 255, 255)
    dot(48, 50, 42, ink); dot(48, 50, 38, body)     # body with outline
    for ex in (35, 61):                             # eyes
        dot(ex, 42, 8, white); dot(ex, 43, 3.5, ink)
    for x in range(32, 65):                         # mouth, depends on emotion
        t = (x - 48) / 16
        if emotion == "happiness":   y = 68 - 8 * t * t
        elif emotion == "sadness":   y = 62 + 8 * t * t
        elif emotion == "anxiety":   y = 64 + (3 if (x // 4) % 2 else 0)
        elif emotion == "anger":     y = 65
        else:                        y = None
        if y is not None: dot(x, y, 1.8, ink)
    if emotion == "fear":
        for a in range(0, 360, 6):
            dot(48 + 7 * math.cos(math.radians(a)), 65 + 7 * math.sin(math.radians(a)), 1.5, ink)
    if emotion == "anger":                          # slanted brows
        for k in range(14):
            dot(27 + k, 30 + k * 0.5, 1.8, ink); dot(69 - k, 30 + k * 0.5, 1.8, ink)
    return png(pix)

def s(v): return {"stringValue": v}

for n in range(1, count + 1):
    emotion = random.choice(EMOTIONS)
    doc_id = "".join(random.choices(string.ascii_letters + string.digits, k=20))
    body = {"writes": [{
        "update": {
            "name": f"projects/{project}/databases/(default)/documents/spawns/{doc_id}",
            "fields": {
                "name": s(random.choice(NAMES)),
                "title": s(random.choice(TITLES)),
                "emotion": s(emotion),
                "size": {"doubleValue": float(random.choice(SIZES))},
                "image": s(base64.b64encode(sprite(emotion)).decode()),
                "status": s("pending"),
            },
        },
        "updateMask": {"fieldPaths": ["name", "title", "emotion", "size", "image", "status"]},
        "updateTransforms": [{"fieldPath": "createdAt", "setToServerValue": "REQUEST_TIME"}],
        "currentDocument": {"exists": False},       # create only, never overwrite
    }]}
    with open(f"{out}/body_{n:04d}.json", "w") as f:
        json.dump(body, f)
PY

URL="https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents:commit?key=${API_KEY}"
ok=0; i=0
for body in "$OUT"/body_*.json; do
  i=$((i + 1))
  if [ "$DRY_RUN" = "1" ]; then
    echo "[$i/$COUNT] dry run: $body ($(wc -c < "$body") bytes)"
    continue
  fi
  code=$(curl -s -o "$OUT/response.txt" -w '%{http_code}' -X POST \
         -H 'Content-Type: application/json' -d @"$body" "$URL")
  if [ "$code" = "200" ]; then
    ok=$((ok + 1)); echo "[$i/$COUNT] created"
  else
    echo "[$i/$COUNT] FAILED (HTTP $code)" >&2
    cat "$OUT/response.txt" >&2; echo >&2
    case "$code" in
      403) echo "Hint: Firestore rules are rejecting the write (check the rules for /spawns)." >&2 ;;
      400) echo "Hint: bad request or invalid API key / project id." >&2 ;;
      404) echo "Hint: Firestore database not created in this project yet." >&2 ;;
    esac
    exit 1
  fi
  [ "$i" -lt "$COUNT" ] && sleep "$DELAY"
done
[ "$DRY_RUN" = "1" ] || echo "done: $ok character(s) written to 'spawns'"