import { firebaseConfig } from "./config.js";

const FIREBASE_VERSION = "10.12.2";
const IMG_SIZE = 96;               // final image is IMG_SIZE x IMG_SIZE PNG
const WRITE_TIMEOUT_MS = 15000;
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

// ---------- Image input ----------
let imageBase64 = null;            // PNG base64, without the "data:image/png;base64," prefix

function handleFile(file) {
  if (!file || !file.type.startsWith("image/")) {
    showError("That doesn't look like an image.");
    return;
  }
  const url = URL.createObjectURL(file);
  const img = new Image();
  img.onload = () => { URL.revokeObjectURL(url); setImage(img); };
  img.onerror = () => { URL.revokeObjectURL(url); showError("Couldn't read that image."); };
  img.src = url;
}

// Shrink to IMG_SIZE x IMG_SIZE, keeping the whole image (padded with transparency).
function setImage(img) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = IMG_SIZE;
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingQuality = "high";
  const scale = Math.min(IMG_SIZE / img.naturalWidth, IMG_SIZE / img.naturalHeight);
  const w = img.naturalWidth * scale;
  const h = img.naturalHeight * scale;
  ctx.drawImage(img, (IMG_SIZE - w) / 2, (IMG_SIZE - h) / 2, w, h);

  const dataUrl = canvas.toDataURL("image/png");
  imageBase64 = dataUrl.split(",")[1];

  $("preview").src = dataUrl;
  $("preview").hidden = false;
  $("drop-text").hidden = true;
  hideError();
}

const dropZone = $("drop-zone");
const fileInput = $("file-input");

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener("change", () => {
  handleFile(fileInput.files[0]);
  fileInput.value = "";
});

dropZone.addEventListener("dragover", (e) => { e.preventDefault(); dropZone.classList.add("dragging"); });
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("dragging"));
dropZone.addEventListener("drop", (e) => {
  e.preventDefault();
  dropZone.classList.remove("dragging");
  handleFile(e.dataTransfer.files[0]);
});

// Paste anywhere on the page (unless typing in a text field)
document.addEventListener("paste", (e) => {
  if ($("create-view").hidden) return;
  const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
  if (!item) return;
  e.preventDefault();
  handleFile(item.getAsFile());
});

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
    image: imageBase64,
    status: "pending",
  };
  // Background story ($("story")) is intentionally never read or sent.

  if (!data.name) return [null, "Give your character a name."];
  if (!data.title) return [null, "Give your character a title."];
  if (!EMOTIONS.includes(data.emotion)) return [null, "Pick an emotion."];
  if (!SIZES.includes(data.size)) return [null, "Pick a size."];
  if (!data.image) return [null, "Add an image of your character."];
  return [data, null];
}

const withTimeout = (p, ms) =>
  Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error("Timed out. Check your connection.")), ms))]);

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
    if (db) {
      await withTimeout(
        fs.addDoc(fs.collection(db, "spawns"), { ...data, createdAt: fs.serverTimestamp() }),
        WRITE_TIMEOUT_MS
      );
    } else {
      console.log("[dry run] Would write to spawns:", {
        ...data,
        image: `<${data.image.length} chars of base64 PNG>`,
        createdAt: "<serverTimestamp>",
      });
      console.log("[dry run] Full image data URL:", "data:image/png;base64," + data.image);
    }
    showCreated(data);
  } catch (error) {
    console.error(error);
    showError(`Couldn't create your character: ${error.code || error.message}`);
  } finally {
    btn.disabled = false;
    btn.textContent = "Create character";
  }
});

// ---------- Created view ----------
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);

function showCreated(data) {
  $("c-fullname").textContent = `${data.name} the ${data.title}`;
  $("c-image").src = "data:image/png;base64," + data.image;
  $("c-emotion").textContent = cap(data.emotion);
  $("c-size").textContent = data.size.toFixed(1);
  $("create-view").hidden = true;
  $("created-view").hidden = false;
  window.scrollTo(0, 0);
}

$("again-btn").addEventListener("click", () => {
  $("create-form").reset();
  imageBase64 = null;
  $("preview").hidden = true;
  $("preview").removeAttribute("src");
  $("drop-text").hidden = false;
  updateHint();
  hideError();
  $("created-view").hidden = true;
  $("create-view").hidden = false;
});
