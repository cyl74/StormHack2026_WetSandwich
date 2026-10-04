export function initDrawing() {
  const canvas = document.getElementById('draw-canvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const color = document.getElementById('pen-color');
  const size = document.getElementById('pen-size');
  const penBtn = document.getElementById('tool-pen');
  const eraserBtn = document.getElementById('tool-eraser');

  let tool = 'pen', drawing = false, dirty = false, last = null;
  const history = [];

  ctx.lineCap = ctx.lineJoin = 'round';

  const pos = (e) => {
    const r = canvas.getBoundingClientRect();
    return {
      x: (e.clientX - r.left) * (canvas.width / r.width),
      y: (e.clientY - r.top) * (canvas.height / r.height),
    };
  };

  const snapshot = () => {
    history.push(ctx.getImageData(0, 0, canvas.width, canvas.height));
    if (history.length > 30) history.shift();
  };

  const style = () => {
    ctx.globalCompositeOperation = tool === 'eraser' ? 'destination-out' : 'source-over';
    ctx.strokeStyle = ctx.fillStyle = color.value;
    ctx.lineWidth = +size.value;
  };

  canvas.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    snapshot();
    drawing = true;
    dirty = true;
    last = pos(e);
    style();
    // dot for single taps
    ctx.beginPath();
    ctx.arc(last.x, last.y, ctx.lineWidth / 2, 0, Math.PI * 2);
    ctx.fill();
  });

  canvas.addEventListener('pointermove', (e) => {
    if (!drawing) return;
    const p = pos(e);
    const mid = { x: (last.x + p.x) / 2, y: (last.y + p.y) / 2 };
    ctx.beginPath();
    ctx.moveTo(last.x, last.y);
    ctx.quadraticCurveTo(last.x, last.y, mid.x, mid.y);
    ctx.stroke();
    last = p;
  });

  const stop = () => { drawing = false; };
  canvas.addEventListener('pointerup', stop);
  canvas.addEventListener('pointercancel', stop);

  const setTool = (t) => {
    tool = t;
    penBtn.classList.toggle('primary', t === 'pen');
    eraserBtn.classList.toggle('primary', t === 'eraser');
  };
  penBtn.onclick = () => setTool('pen');
  eraserBtn.onclick = () => setTool('eraser');

  document.getElementById('undo-btn').onclick = () => {
    const prev = history.pop();
    if (prev) ctx.putImageData(prev, 0, 0);
    if (!history.length) dirty = false;
  };

  const clear = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    history.length = 0;
    dirty = false;
  };
  document.getElementById('clear-btn').onclick = clear;

  return {
    isEmpty: () => !dirty,
    clear,
    toBase64: (size) => {
      const out = document.createElement('canvas');
      out.width = out.height = size;
      const octx = out.getContext('2d');
      octx.imageSmoothingQuality = 'high';
      octx.drawImage(canvas, 0, 0, size, size);
      return out.toDataURL('image/png').split(',')[1];
    },
  };
}