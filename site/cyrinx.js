/* Cyrinx site instrument: synthesizes an actual cyrinx bulk-PHY frame
 * (chirp 2->16 kHz, guard, 2 sync symbols, OFDM payload symbols; 48 kHz,
 * NFFT 2048, CP 768, bins 47..981 = 1.1-23 kHz) and renders its spectrogram
 * on canvas. The "listen" button plays the very same samples via WebAudio.
 * No libraries; deterministic (seeded PRNG). */
'use strict';

/* ---------- deterministic PRNG (mulberry32) ---------- */
function rng(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ---------- iterative radix-2 complex FFT (in place) ---------- */
function fft(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let t = re[i]; re[i] = re[j]; re[j] = t;
      t = im[i]; im[i] = im[j]; im[j] = t;
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = (-2 * Math.PI) / len;
    const wr = Math.cos(ang), wi = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let cwr = 1, cwi = 0;
      for (let k = 0; k < len / 2; k++) {
        const ur = re[i + k], ui = im[i + k];
        const vr = re[i + k + len / 2] * cwr - im[i + k + len / 2] * cwi;
        const vi = re[i + k + len / 2] * cwi + im[i + k + len / 2] * cwr;
        re[i + k] = ur + vr; im[i + k] = ui + vi;
        re[i + k + len / 2] = ur - vr; im[i + k + len / 2] = ui - vi;
        const nwr = cwr * wr - cwi * wi;
        cwi = cwr * wi + cwi * wr; cwr = nwr;
      }
    }
  }
}

/* ---------- frame synthesis (mirrors modem.py geometry) ---------- */
const SR = 48000, NFFT = 2048, CP = 768, SYM = NFFT + CP;
const CHIRP_LEN = 4096, GUARD = 2048;
const BIN_LO = 47, BIN_HI = 981; // 1.1-23 kHz at 23.4375 Hz/bin
const N_DATA_SYMS = 8;

function synthesizeFrame() {
  const nSyms = 2 + N_DATA_SYMS;
  const total = CHIRP_LEN + GUARD + nSyms * SYM;
  const out = new Float32Array(total);
  // chirp 2->16 kHz with raised-cosine edges
  const f0 = 2000, f1 = 16000, T = CHIRP_LEN / SR;
  for (let i = 0; i < CHIRP_LEN; i++) {
    const t = i / SR;
    let env = 1;
    if (i < 128) env = 0.5 - 0.5 * Math.cos((Math.PI * i) / 128);
    if (i >= CHIRP_LEN - 128) env = 0.5 - 0.5 * Math.cos((Math.PI * (CHIRP_LEN - 1 - i)) / 128);
    out[i] = 0.5 * env * Math.sin(2 * Math.PI * (f0 * t + (0.5 * (f1 - f0) * t * t) / T));
  }
  // OFDM symbols: unit-magnitude random-phase carriers on the used bins
  const rand = rng(0x5eed);
  const re = new Float64Array(NFFT), im = new Float64Array(NFFT);
  let pos = CHIRP_LEN + GUARD;
  let peak = 0;
  for (let s = 0; s < nSyms; s++) {
    re.fill(0); im.fill(0);
    for (let b = BIN_LO; b <= BIN_HI; b++) {
      const ph = Math.PI / 4 + (Math.PI / 2) * Math.floor(rand() * 4);
      // hermitian spectrum -> real time signal; ifft(X) = conj(fft(conj(X)))/N
      re[b] = Math.cos(ph); im[b] = -Math.sin(ph);
      re[NFFT - b] = re[b]; im[NFFT - b] = -im[b];
    }
    fft(re, im);
    for (let i = 0; i < NFFT; i++) {
      const v = re[i] / NFFT;
      out[pos + CP + i] = v;
      if (Math.abs(v) > peak) peak = Math.abs(v);
    }
    for (let i = 0; i < CP; i++) out[pos + i] = out[pos + NFFT + i]; // cyclic prefix
    pos += SYM;
  }
  // normalize OFDM region to +/-0.5 like the chirp
  const g = 0.5 / peak;
  for (let i = CHIRP_LEN + GUARD; i < total; i++) out[i] *= g;
  return out;
}

/* ---------- magma colormap ---------- */
const MAGMA = [
  [0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122],
  [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191],
];
function magma(x) {
  const t = Math.min(1, Math.max(0, x)) * (MAGMA.length - 1);
  const i = Math.min(MAGMA.length - 2, Math.floor(t));
  const f = t - i;
  return [
    MAGMA[i][0] + f * (MAGMA[i + 1][0] - MAGMA[i][0]),
    MAGMA[i][1] + f * (MAGMA[i + 1][1] - MAGMA[i][1]),
    MAGMA[i][2] + f * (MAGMA[i + 1][2] - MAGMA[i][2]),
  ];
}

/* ---------- spectrogram ---------- */
const WIN = 512, FBINS = WIN / 2;
function computeSpectrogram(samples, nCols) {
  const hop = Math.max(1, Math.floor((samples.length - WIN) / (nCols - 1)));
  const hann = new Float64Array(WIN);
  for (let i = 0; i < WIN; i++) hann[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / WIN);
  const img = new Uint8ClampedArray(nCols * FBINS * 4);
  const re = new Float64Array(WIN), im = new Float64Array(WIN);
  const db = new Float64Array(nCols * FBINS);
  let dbMax = -Infinity;
  for (let c = 0; c < nCols; c++) {
    const off = c * hop;
    for (let i = 0; i < WIN; i++) {
      re[i] = (samples[off + i] || 0) * hann[i];
      im[i] = 0;
    }
    fft(re, im);
    for (let k = 0; k < FBINS; k++) {
      const mag = Math.sqrt(re[k] * re[k] + im[k] * im[k]);
      const v = 20 * Math.log10(mag + 1e-9);
      db[c * FBINS + k] = v;
      if (v > dbMax) dbMax = v;
    }
  }
  const RANGE = 62; // dB below the frame peak mapped onto the colormap
  for (let c = 0; c < nCols; c++) {
    for (let k = 0; k < FBINS; k++) {
      const x = (db[c * FBINS + k] - dbMax + RANGE) / RANGE;
      const [r, g, b] = magma(x);
      const row = FBINS - 1 - k; // low freq at bottom
      const p = (row * nCols + c) * 4;
      img[p] = r; img[p + 1] = g; img[p + 2] = b; img[p + 3] = 255;
    }
  }
  return img;
}

/* ---------- hero instrument ---------- */
const FRAME = synthesizeFrame();
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function setupInstrument() {
  const canvas = document.getElementById('spectrogram');
  if (!canvas) return;
  const off = document.createElement('canvas');
  let progress = reducedMotion ? 1 : 0;
  let playhead = -1;

  function render() {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(300, Math.floor(canvas.clientWidth * dpr));
    const h = Math.max(120, Math.floor(canvas.clientHeight * dpr));
    canvas.width = w; canvas.height = h;
    const nCols = Math.min(1200, Math.floor(w / 2));
    off.width = nCols; off.height = FBINS;
    off.getContext('2d').putImageData(new ImageData(computeSpectrogram(FRAME, nCols), nCols, FBINS), 0, 0);
    paint();
  }

  function paint() {
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;
    ctx.fillStyle = '#12101A';
    ctx.fillRect(0, 0, w, h);
    ctx.imageSmoothingEnabled = true;
    const cut = Math.floor(w * progress);
    if (cut > 0) {
      ctx.drawImage(off, 0, 0, off.width * progress, off.height, 0, 0, cut, h);
    }
    const px = playhead >= 0 ? playhead : (progress < 1 ? progress : -1);
    if (px >= 0 && px <= 1) {
      ctx.fillStyle = '#E8890C';
      ctx.fillRect(Math.floor(w * px), 0, Math.max(2, Math.floor(w / 600)), h);
    }
  }

  // anatomy label widths from real sample offsets
  const total = FRAME.length;
  const widths = {
    'anat-chirp': CHIRP_LEN / total,
    'anat-guard': GUARD / total,
    'anat-sync': (2 * SYM) / total,
    'anat-data': (N_DATA_SYMS * SYM) / total,
  };
  for (const [id, frac] of Object.entries(widths)) {
    const el = document.getElementById(id);
    if (el) el.style.width = (frac * 100).toFixed(2) + '%';
  }

  render();
  let resizeTimer;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(render, 150);
  });

  // reveal animation
  if (!reducedMotion) {
    const t0 = performance.now(), dur = 1800;
    (function step(t) {
      progress = Math.min(1, (t - t0) / dur);
      progress = 1 - Math.pow(1 - progress, 2); // ease-out
      paint();
      if (progress < 1) requestAnimationFrame(step);
    })(t0);
  }

  // listen: play the exact synthesized samples
  const btn = document.getElementById('listen');
  let audioCtx = null, srcNode = null, playing = false, rafId = 0;
  function stop() {
    if (srcNode) { try { srcNode.stop(); } catch (e) { /* already stopped */ } srcNode = null; }
    playing = false;
    btn.setAttribute('aria-pressed', 'false');
    btn.querySelector('.glyph').textContent = '▶';
    playhead = -1;
    cancelAnimationFrame(rafId);
    paint();
  }
  btn.addEventListener('click', () => {
    if (playing) { stop(); return; }
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const buf = audioCtx.createBuffer(1, FRAME.length, SR);
    buf.copyToChannel(FRAME, 0);
    srcNode = audioCtx.createBufferSource();
    srcNode.buffer = buf;
    const gain = audioCtx.createGain();
    gain.gain.value = 0.35;
    srcNode.connect(gain).connect(audioCtx.destination);
    const startAt = audioCtx.currentTime;
    srcNode.onended = stop;
    srcNode.start();
    playing = true;
    btn.setAttribute('aria-pressed', 'true');
    btn.querySelector('.glyph').textContent = '◼';
    const durS = FRAME.length / SR;
    (function follow() {
      if (!playing) return;
      playhead = Math.min(1, (audioCtx.currentTime - startAt) / durS);
      paint();
      rafId = requestAnimationFrame(follow);
    })();
  });
}

/* ---------- constellation with EVM slider ---------- */
function setupConstellation() {
  const canvas = document.getElementById('constellation');
  const slider = document.getElementById('evm');
  const out = document.getElementById('evm-out');
  const verdict = document.getElementById('evm-verdict');
  if (!canvas || !slider) return;

  const LEVELS = [-3, -1, 1, 3].map((v) => v / Math.sqrt(10)); // unit avg power
  let jitterSeed = 1;

  function draw() {
    const evm = parseFloat(slider.value);
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = Math.max(280, Math.floor(canvas.clientWidth * dpr));
    const h = Math.floor(canvas.clientHeight * dpr) || Math.floor(w * 0.575);
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#12101A';
    ctx.fillRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2;
    const scale = Math.min(w, h) * 0.36;
    // decision boundaries
    ctx.strokeStyle = 'rgba(201,196,212,0.18)';
    ctx.lineWidth = 1;
    for (const v of [-2, 0, 2].map((x) => x / Math.sqrt(10))) {
      ctx.beginPath();
      ctx.moveTo(cx + v * scale, cy - scale * 1.35); ctx.lineTo(cx + v * scale, cy + scale * 1.35);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(cx - scale * 1.6, cy + v * scale); ctx.lineTo(cx + scale * 1.6, cy + v * scale);
      ctx.stroke();
    }
    // noisy received points (box-muller with seeded rng)
    const rand = rng(jitterSeed);
    const sigma = evm / Math.SQRT2;
    ctx.fillStyle = 'rgba(232,137,12,0.5)';
    for (const I of LEVELS) for (const Q of LEVELS) {
      for (let n = 0; n < 26; n++) {
        const u1 = Math.max(1e-9, rand()), u2 = rand();
        const r = Math.sqrt(-2 * Math.log(u1));
        const x = I + sigma * r * Math.cos(2 * Math.PI * u2);
        const y = Q + sigma * r * Math.sin(2 * Math.PI * u2);
        ctx.beginPath();
        ctx.arc(cx + x * scale, cy - y * scale, Math.max(1.2, w / 500), 0, 2 * Math.PI);
        ctx.fill();
      }
    }
    // ideal points
    ctx.fillStyle = '#FCFDBF';
    for (const I of LEVELS) for (const Q of LEVELS) {
      ctx.beginPath();
      ctx.arc(cx + I * scale, cy - Q * scale, Math.max(2, w / 320), 0, 2 * Math.PI);
      ctx.fill();
    }
    out.textContent = evm.toFixed(3);
    let msg, cls;
    if (evm < 0.08) { msg = 'clean enough for 64-QAM — a regime these transducers never delivered'; cls = 'ok'; }
    else if (evm <= 0.13) { msg = 'the measured sweet spot: 16-QAM r¾ decodes → 39–48 kbps'; cls = 'ok'; }
    else if (evm <= 0.16) { msg = '16-QAM getting marginal; the sounder steps down a tier'; cls = ''; }
    else { msg = 'measured at EVM 0.173: 64-QAM decoded 0 of 339 blocks'; cls = 'bad'; }
    verdict.textContent = msg;
    verdict.className = 'verdict ' + cls;
  }

  slider.addEventListener('input', draw);
  window.addEventListener('resize', draw);
  draw();
  if (!reducedMotion) {
    setInterval(() => { jitterSeed = (jitterSeed + 1) | 0; draw(); }, 600);
  }
}

setupInstrument();
setupConstellation();
