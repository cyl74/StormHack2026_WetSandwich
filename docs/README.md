# Doodle Arena – website

Plain HTML/CSS/JS, no build step. Desktop only.

| File | What it is |
|---|---|
| `index.html` | Create page + "character created" view |
| `app.js` | Image input, validation, Firestore write |
| `stream.html` | Embedded Twitch player |
| `config.js` | Twitch channel + Firebase config (the only file you edit) |
| `style.css` | Styles |
| `firestore.rules` | Security rules (create-only, validated) |

## 1. Run locally (dry run)

```
cd doodle-arena-web
python -m http.server 8000
```

Open http://localhost:8000 in Chrome. Don't open `index.html` by double-clicking it; module
scripts and the Twitch embed need a real hostname.

With `firebaseConfig = null` in `config.js`, the page is in **dry-run** mode (yellow badge).
"Create character" logs the JSON to the DevTools console (F12) instead of sending it.

## 2. Connect Firestore

1. Firebase console → create a project → Build → Firestore Database → create.
2. Project settings → Your apps → add a Web app → copy the config object.
3. Paste it into `config.js` as `firebaseConfig`. Reload; the dry-run badge disappears.
4. Publish `firestore.rules` (paste into Firestore → Rules, or deploy with the CLI in step 3).

No local emulator is needed: `localhost` talks to the real project.

## 3. Host

```
npm install -g firebase-tools
firebase login
firebase init hosting      # public directory: .   single-page app: No
firebase deploy
```

The Twitch embed uses the page's own hostname as `parent`, so it works on both
`localhost` and your `*.web.app` domain without changes.

## Document written to `spawns`

```json
{ "name": "Bob", "title": "Builder", "emotion": "anxiety", "size": 1.5,
  "image": "<base64 PNG, 96x96, no data: prefix>",
  "status": "pending", "createdAt": <serverTimestamp> }
```

- `size` is a number: 0.5, 1, 1.5 or 2.
- `image` is raw base64. In Python: `base64.b64decode(doc["image"])` gives the PNG bytes.
  Transparency is kept if the original had it; non-square images are padded with transparency.
- The background story is never sent.
- Full name for display is `f"{name} the {title}"`.
