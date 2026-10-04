import { firebaseConfig } from "./config.js";
import { initDrawing } from "./draw.js";

const FIREBASE_VERSION = "10.12.2";
const IMG_SIZE = 96;               // final image is IMG_SIZE x IMG_SIZE PNG
const WRITE_TIMEOUT_MS = 15000;
const AI_API_URL = "/api/analyze";
const EMOTIONS = ["happiness", "sadness", "fear", "anger", "anxiety"];
const SIZES = [0.5, 1, 1.5, 2];

const $ = (id) => document.getElementById(id);

// ---------- Firebase (or dry run) ----------
let db = null;
let fs = null;

const ready = (async () => {
  if (!firebaseConfig) {
    $("mode-badge").hidden = false;
    console.info("[Doodle Arena] No firebaseConfig in config.js: dry-run mode.");
    return;
  }
  const base = `https://www.gstatic.com/firebasejs/${FIREBASE_VERSION}`;
  const { initializeApp } = await import(`${base}/firebase-app.js`);
  fs = await import(`${base}/firebase-firestore.js`);
  db = fs.getFirestore(initializeApp(firebaseConfig));
})();

// ---------- Drawing ----------
const drawing = initDrawing();

// ---------- Live "Name the Title" hint ----------
function updateHint() {
  const name = $("name").value.trim() || "Bob";
  const title = $("title").value.trim() || "Builder";
  $("fullname-hint").textContent = `${name} the ${title}`;
}
$("name").addEventListener("input", updateHint);
$("title").addEventListener("input", updateHint);

// ---------- Submit ----------
function showError(msg) { $("error").textContent = msg; $("error").hidden = false; }
function hideError() { $("error").hidden = true; }

function collect() {
  const data = {
    name: $("name").value.trim(),
    title: $("title").value.trim(),
    emotion: $("emotion").value,
    size: Number($("size").value),
    image: drawing.isEmpty() ? null : drawing.toBase64(IMG_SIZE),
    status: "pending",
  };
  // Background story ($("story")) is intentionally never read or sent.

  if (!data.name) return [null, "Give your character a name."];
  if (!data.title) return [null, "Give your character a title."];
  if (!EMOTIONS.includes(data.emotion)) return [null, "Pick an emotion."];
  if (!SIZES.includes(data.size)) return [null, "Pick a size."];
  if (!data.image) return [null, "Draw your character first."];
  return [data, null];
  };

const withTimeout = (p, ms) =>
  Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("Timed out. Check your connection.")), ms))]);

async function analyzeImageWithAI(imageBase64) {
  const response = await withTimeout(fetch(AI_API_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image: imageBase64 }),
  }), 60000);

  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch (error) {
    throw new Error("AI returned invalid JSON.");
  }

  if (!response.ok) {
    throw new Error(payload.error || "AI analysis failed.");
  }

  return payload;
}

$("create-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const [data, err] = collect();
  if (err) { showError(err); return; }
  hideError();

  const btn = $("create-btn");
  btn.disabled = true;
  btn.textContent = "Creating…";
  try {
    await ready;
    const aiResult = await analyzeImageWithAI(data.image);
    const savedData = {
      ...data,
      analysis: aiResult.analysis,
      traits: aiResult.traits,
      createdAt: new Date().toISOString(),
    };

    if (db) {
      await withTimeout(
        fs.addDoc(fs.collection(db, "spawns"), { ...savedData, createdAt: fs.serverTimestamp() }),
        WRITE_TIMEOUT_MS
      );
    } else {
      console.log("[dry run] AI analysis:", aiResult);
      console.log("[dry run] Would write to spawns:", {
        ...savedData,
        image: `<${data.image.length} chars of base64 PNG>`,
        createdAt: "<serverTimestamp>",
      });
      console.log("[dry run] Full image data URL:", "data:image/png;base64," + data.image);
    }
    showCreated(data, aiResult);
  } catch (error) {
    console.error(error);
    showError(`Couldn't create your character: ${error.message || error.code || error}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create character";
  }
});

// ---------- Created view ----------
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function showCreated(data, aiResult) {
  $("c-fullname").textContent = `${data.name} the ${data.title}`;
  $("c-image").src = "data:image/png;base64," + data.image;
  $("c-emotion").textContent = cap(data.emotion);
  $("c-size").textContent = data.size.toFixed(1);
  $("c-analysis").textContent = JSON.stringify(aiResult, null, 2);
  $("create-view").hidden = true;
  $("created-view").hidden = false;
  window.scrollTo(0, 0);
}

$("again-btn").addEventListener("click", () => {
  $("create-form").reset();
  drawing.clear();
  $("c-analysis").textContent = "";
  updateHint();
  hideError();
  $("created-view").hidden = true;
  $("create-view").hidden = false;
});
