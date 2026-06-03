/* ============================================================
   Jarvis – Three.js Orb Renderer + WebSocket Client
   ============================================================ */

"use strict";

// ── State machine ──────────────────────────────────────────────────────────────
const STATES = { idle: 0, listening: 1, thinking: 2, speaking: 3, operator: 2 };
const STATE_LABELS = {
  starting:  "Loading models…",
  idle:      "Standby · always listening",
  listening: "Listening…",
  thinking:  "Processing…",
  speaking:  "Speaking",
  operator:  "Operating…",
};

let operatorRunning  = false;
let _respondingTo    = "";      // query the next JARVIS message is answering

let currentState    = STATES.idle;
let targetAmplitude = 0;
let smoothAmplitude = 0;

// ── DOM refs ───────────────────────────────────────────────────────────────────
const canvasWrap     = document.getElementById("canvas-wrap");
const statusLabel    = document.getElementById("status-label");
const queryText      = document.getElementById("query-text");
const operatorStatus = document.getElementById("operator-status");
const commLog        = document.getElementById("comm-log");
const activityLog    = document.getElementById("activity-log");
const ampFill        = document.getElementById("amp-fill");
const svalDate       = document.getElementById("sval-date");
const svalUptime     = document.getElementById("sval-uptime");
const clockEl        = document.getElementById("clock");

// ── Live activity feed (left panel, always visible) ─────────────────────────────
const ACT_MAX = 80;   // max entries before oldest are pruned

function addActivity(kind, icon, body) {
  // kind: act-think | act-tool | act-result | act-answer | act-state | act-error
  if (!activityLog) return;

  const row  = document.createElement("div");
  row.className = `act ${kind}`;

  const ic   = document.createElement("span");
  ic.className = "act-icon";
  ic.textContent = icon;

  const bd   = document.createElement("span");
  bd.className = "act-body";
  bd.textContent = body;

  row.appendChild(ic);
  row.appendChild(bd);
  activityLog.appendChild(row);

  // Prune old entries
  while (activityLog.children.length > ACT_MAX) {
    activityLog.removeChild(activityLog.firstChild);
  }
  activityLog.scrollTop = activityLog.scrollHeight;
}

// ── Clock + uptime ─────────────────────────────────────────────────────────────
const sessionStart = Date.now();

function pad2(n) { return String(n).padStart(2, "0"); }

function updateClock() {
  const now = new Date();
  clockEl.textContent = `${pad2(now.getHours())}:${pad2(now.getMinutes())}:${pad2(now.getSeconds())}`;
  svalDate.textContent = now.toLocaleDateString("en-US", { month:"short", day:"numeric" }).toUpperCase();

  const elapsed = Math.floor((Date.now() - sessionStart) / 1000);
  const m = Math.floor(elapsed / 60), s = elapsed % 60;
  svalUptime.textContent = `${pad2(m)}:${pad2(s)}`;
}
setInterval(updateClock, 1000);
updateClock();

// ── Comm log helpers ───────────────────────────────────────────────────────────
let msgCount = 0;

function addMessage(who, text, context) {
  msgCount++;
  const div  = document.createElement("div");
  div.className = `msg ${who}`;

  const label = document.createElement("span");
  label.className = "msg-who";
  label.textContent = who === "you" ? "YOU" : "JARVIS";

  // Optional "responding to: X" context shown as a subtitle on JARVIS messages
  if (context) {
    const ctx = document.createElement("span");
    ctx.className = "msg-context";
    ctx.textContent = `↳ ${context}`;
    div.appendChild(label);
    div.appendChild(ctx);
  } else {
    div.appendChild(label);
  }

  const body = document.createElement("span");
  body.className = "msg-body";
  body.textContent = text;

  div.appendChild(body);
  commLog.appendChild(div);

  // Keep last 60 messages to avoid memory growth
  while (commLog.children.length > 60) commLog.removeChild(commLog.firstChild);

  // Auto-scroll to bottom
  commLog.scrollTop = commLog.scrollHeight;
}

// ── Three.js setup ─────────────────────────────────────────────────────────────
function canvasW() { return canvasWrap.clientWidth  || 480; }
function canvasH() { return canvasWrap.clientHeight || 380; }

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
renderer.setSize(canvasW(), canvasH());
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setClearColor(0x000000, 0);
canvasWrap.appendChild(renderer.domElement);

const scene  = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(55, canvasW() / canvasH(), 0.1, 100);
camera.position.set(0, 0, 3.2);

const clock = new THREE.Clock();

// ── Shaders ────────────────────────────────────────────────────────────────────
const vertexShader = /* glsl */`
  uniform float time;
  uniform float amp;
  uniform float stateF;

  varying vec3 vNormal;
  varying vec3 vPos;
  varying vec2 vUv;

  void main() {
    vUv     = uv;
    vNormal = normalize(normalMatrix * normal);
    vPos    = position;

    vec3 p  = position;
    float disp = 0.0;

    // idle: gentle breath
    disp += sin(p.y * 3.0 + time * 0.8) * 0.008;

    // listening: ripple
    if (stateF > 0.4)
      disp += sin(p.y * 6.0 + time * 2.5) * 0.018 * stateF;

    // thinking: complex surface waves
    if (stateF > 1.4)
      disp += cos(p.x * 5.0 + time * 3.0) * 0.02 * (stateF - 1.0);

    // speaking: amplitude-driven deform
    if (stateF > 2.4)
      disp += sin(p.y * 8.0 + time * 4.0) * 0.035 * amp
            + cos(p.z * 7.0 + time * 3.5) * 0.025 * amp;

    p += normal * disp;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
  }
`;

const fragmentShader = /* glsl */`
  uniform float time;
  uniform float amp;
  uniform float stateF;

  varying vec3 vNormal;
  varying vec3 vPos;
  varying vec2 vUv;

  float hash21(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }

  vec3 stateColor(float s) {
    if (s < 0.5) return vec3(0.10, 0.42, 1.00);   // idle   – blue
    if (s < 1.5) return vec3(0.00, 0.90, 0.80);   // listen – cyan
    if (s < 2.5) return vec3(0.54, 0.17, 1.00);   // think  – violet
    return             vec3(0.28, 0.72, 1.00);     // speak  – sky blue
  }

  void main() {
    vec3 n = normalize(vNormal);
    vec3 view = vec3(0.0, 0.0, 1.0);

    // Fresnel rim
    float fres = pow(1.0 - abs(dot(n, view)), 2.5);

    vec3 col = stateColor(stateF);

    // Iridescent shimmer
    float iri = sin(dot(n, vec3(1.0, 0.5, 0.2)) * 8.0 + time * 1.2) * 0.5 + 0.5;
    vec3 iriCol = mix(col, vec3(0.9, 0.5, 1.0), 0.35);
    col = mix(col, iriCol, iri * fres * 0.7);

    // Hex-cell flicker
    vec2 cell = floor(vUv * 12.0 + vec2(time * 0.05));
    float h = hash21(cell);
    float flicker = step(0.88, h) * (sin(time * 3.0 + h * 6.28) * 0.5 + 0.5);
    col += flicker * col * 0.6;

    // Horizontal scan lines
    float scan = sin(vUv.y * 180.0 - time * 2.5) * 0.035 + 0.965;
    col *= scan;

    // Edge glow
    col += fres * col * 1.1;

    // Amplitude brightness burst
    col *= 1.0 + amp * 0.45;

    // Idle breathing
    float breath = sin(time * 0.7) * 0.08 + 0.92;
    if (stateF < 0.5) col *= breath;

    float alpha = 0.55 + fres * 0.45;
    gl_FragColor = vec4(col, alpha);
  }
`;

// ── Main orb ───────────────────────────────────────────────────────────────────
const orbUniforms = {
  time:   { value: 0 },
  amp:    { value: 0 },
  stateF: { value: 0 },
};

const orbMat = new THREE.ShaderMaterial({
  uniforms:       orbUniforms,
  vertexShader,
  fragmentShader,
  transparent:    true,
  blending:       THREE.AdditiveBlending,
  depthWrite:     false,
  side:           THREE.FrontSide,
});

const orb = new THREE.Mesh(new THREE.SphereGeometry(1, 96, 96), orbMat);
scene.add(orb);

// ── Glow layers ────────────────────────────────────────────────────────────────
const glowLayers = [];
const glowColors = [0x1a6bff, 0x0ae8cc, 0x8b2bff, 0x4db8ff];

function buildGlow() {
  glowLayers.forEach(g => scene.remove(g));
  glowLayers.length = 0;
  const col = glowColors[currentState] || 0x1a6bff;
  for (let i = 0; i < 5; i++) {
    const mat = new THREE.MeshBasicMaterial({
      color:       col,
      transparent: true,
      opacity:     0.022 - i * 0.003,
      blending:    THREE.AdditiveBlending,
      depthWrite:  false,
      side:        THREE.BackSide,
    });
    const mesh = new THREE.Mesh(new THREE.SphereGeometry(1.12 + i * 0.18, 32, 32), mat);
    scene.add(mesh);
    glowLayers.push(mesh);
  }
}
buildGlow();

// ── Wireframe shell ────────────────────────────────────────────────────────────
const wireMat = new THREE.MeshBasicMaterial({
  color:       0x3399ff,
  wireframe:   true,
  transparent: true,
  opacity:     0.06,
  blending:    THREE.AdditiveBlending,
  depthWrite:  false,
});
const wire = new THREE.Mesh(new THREE.SphereGeometry(1.05, 24, 16), wireMat);
scene.add(wire);

// ── Orbit rings ────────────────────────────────────────────────────────────────
function makeRing(radius, tube, rx, ry, color, opacity) {
  const mat = new THREE.MeshBasicMaterial({
    color, transparent: true, opacity,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const mesh = new THREE.Mesh(new THREE.TorusGeometry(radius, tube, 8, 80), mat);
  mesh.rotation.x = rx;
  mesh.rotation.y = ry;
  scene.add(mesh);
  return mesh;
}

const ring1 = makeRing(1.38, 0.005, Math.PI / 2.3, 0.3,  0x00e8cc, 0.35);
const ring2 = makeRing(1.55, 0.003, Math.PI / 4,   1.0,  0x1a6bff, 0.25);
const ring3 = makeRing(1.72, 0.002, Math.PI / 6,  -0.8,  0x8b2bff, 0.15);

// ── Dot particles on ring1 ─────────────────────────────────────────────────────
const DOT_COUNT = 6;
const dots = [];
for (let i = 0; i < DOT_COUNT; i++) {
  const geo = new THREE.SphereGeometry(0.018, 6, 6);
  const mat = new THREE.MeshBasicMaterial({
    color: 0x00e8cc, transparent: true, opacity: 0.9,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const dot = new THREE.Mesh(geo, mat);
  scene.add(dot);
  dots.push({ mesh: dot, offset: (i / DOT_COUNT) * Math.PI * 2 });
}

// ── Ambient star particles ─────────────────────────────────────────────────────
(function buildStars() {
  const count = 120;
  const pos = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const theta = Math.random() * Math.PI * 2;
    const phi   = Math.acos(2 * Math.random() - 1);
    const r     = 2.5 + Math.random() * 1.5;
    pos[i * 3]     = r * Math.sin(phi) * Math.cos(theta);
    pos[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    pos[i * 3 + 2] = r * Math.cos(phi);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const mat = new THREE.PointsMaterial({
    color: 0x4488ff, size: 0.025, transparent: true, opacity: 0.4,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  scene.add(new THREE.Points(geo, mat));
})();

// ── Animation helpers ──────────────────────────────────────────────────────────
const TARGET_STATE = { value: 0 };
let orbScale = 1.0;
let targetScale = 1.0;

function setVisualState(stateIdx) {
  TARGET_STATE.value = stateIdx;
  buildGlow();
}

// ── Main render loop ───────────────────────────────────────────────────────────
function animate() {
  requestAnimationFrame(animate);

  const t = clock.getElapsedTime();

  // Smooth amplitude
  smoothAmplitude += (targetAmplitude - smoothAmplitude) * 0.12;

  // Smooth state float
  orbUniforms.stateF.value += (TARGET_STATE.value - orbUniforms.stateF.value) * 0.06;
  orbUniforms.time.value    = t;
  orbUniforms.amp.value     = smoothAmplitude;

  // Scale: breath + amplitude swell
  const breath = 1 + Math.sin(t * 0.9) * 0.018;
  targetScale  = breath + smoothAmplitude * 0.45;
  orbScale    += (targetScale - orbScale) * 0.1;
  orb.scale.setScalar(orbScale);

  // Rotation
  orb.rotation.y  += 0.002 * (1 + smoothAmplitude * 2);
  orb.rotation.x  = Math.sin(t * 0.3) * 0.08;
  wire.rotation.y -= 0.0035;
  wire.rotation.z += 0.0012;

  // Rings
  ring1.rotation.z += 0.004;
  ring2.rotation.x += 0.003;
  ring3.rotation.y += 0.002 + smoothAmplitude * 0.01;

  // Orbit dots
  const dotRadius = 1.38;
  const tiltX = Math.PI / 2.3;
  const tiltY = 0.3;
  dots.forEach(({ mesh, offset }) => {
    const angle = t * 0.5 + offset;
    const x = Math.cos(angle) * dotRadius;
    const z = Math.sin(angle) * dotRadius;
    mesh.position.set(
      x * Math.cos(tiltY),
      z * Math.sin(tiltX),
      x * Math.sin(tiltY) + z * Math.cos(tiltX)
    );
    mesh.material.opacity = 0.5 + smoothAmplitude * 0.5;
  });

  // Glow pulse
  const glowPulse = 1 + smoothAmplitude * 0.4 + Math.sin(t * 2) * 0.05;
  glowLayers.forEach((g, i) => {
    g.scale.setScalar(orbScale * glowPulse * (1 + i * 0.01));
  });

  // Amplitude bar in left panel
  ampFill.style.width = `${Math.min(smoothAmplitude * 120, 100)}%`;

  renderer.render(scene, camera);
}
animate();

// ── Resize ─────────────────────────────────────────────────────────────────────
window.addEventListener("resize", () => {
  renderer.setSize(canvasW(), canvasH());
  camera.aspect = canvasW() / canvasH();
  camera.updateProjectionMatrix();
});

// ── Operator status helpers ────────────────────────────────────────────────────
let operatorFadeTimer = null;

function showOperatorStatus(text) {
  if (!operatorStatus) return;
  // Strip "[operator] " prefix for display
  operatorStatus.textContent = text.replace(/^\[operator\]\s*/i, "");
  clearTimeout(operatorFadeTimer);
  operatorFadeTimer = setTimeout(() => {
    if (!operatorRunning) operatorStatus.style.opacity = "0";
  }, 5000);
}

function startOperatorMode() {
  operatorRunning = true;
  document.body.className = "state-operator";
  statusLabel.textContent = STATE_LABELS.operator;
  setVisualState(STATES.thinking);   // purple orb while operating
}

function stopOperatorMode() {
  operatorRunning = false;
  if (operatorStatus) {
    operatorStatus.style.opacity = "0";
    operatorStatus.textContent   = "";
  }
  // Body class will be reset by the next "state" message from backend
}

// ── UI helpers ─────────────────────────────────────────────────────────────────
function applyState(state) {
  if (operatorRunning) return;       // don't override operator state mid-task
  const s = state.toLowerCase();
  document.body.className = `state-${s}`;

  const idx = STATES[s] !== undefined ? STATES[s] : STATES.idle;
  currentState = idx;
  setVisualState(idx);

  statusLabel.textContent = STATE_LABELS[state] || STATE_LABELS.idle;
}

function setQueryText(txt) {
  queryText.textContent = txt ? `"${txt}"` : "";
}

// ── WebSocket ──────────────────────────────────────────────────────────────────
let ws = null;
let reconnectTimer = null;

function connect() {
  if (ws) return;
  ws = new WebSocket("ws://127.0.0.1:8765/ws");

  ws.onopen = () => {
    console.log("[WS] connected");
    statusLabel.textContent = "Loading models…";
  };

  ws.onmessage = (evt) => {
    let msg;
    try { msg = JSON.parse(evt.data); } catch { return; }

    switch (msg.type) {
      case "state":
        applyState(msg.value);
        if (msg.value === "idle") {
          targetAmplitude = 0;
          setQueryText("");
        }
        break;

      case "transcript":
        setQueryText(msg.text);
        addMessage("you", msg.text);
        addActivity("act-state", "🎤", `You: "${msg.text}"`);
        break;

      case "response":
        addMessage("jarvis", msg.text, _respondingTo);
        addActivity("act-answer", "💬", msg.text);
        _respondingTo = "";
        break;

      case "responding_to":
        _respondingTo = msg.text || "";
        setQueryText("↳ " + _respondingTo);
        addActivity("act-think", "↳", `Answering: "${(msg.text || "").slice(0, 60)}"`);
        break;

      case "bg_thinking":
        if (!operatorRunning) {
          statusLabel.textContent = `Processing: "${(msg.query || "").slice(0, 35)}…"`;
        }
        addActivity("act-think", "⚙", `Turn ${msg.tid ?? ""}: "${(msg.query || "").slice(0, 55)}"`);
        break;

      case "amplitude":
        targetAmplitude = Math.min(1, msg.value || 0);
        break;

      case "ready":
        applyState("idle");
        addActivity("act-state", "✓", "Pipeline ready");
        break;

      case "log":
        statusLabel.textContent = msg.text || "";
        addActivity("act-state", "ℹ", msg.text || "");
        break;

      case "error":
        statusLabel.textContent = "ERR: " + (msg.text || "").slice(0, 40);
        console.error("[Jarvis]", msg.text);
        addActivity("act-error", "✗", msg.text || "error");
        break;

      case "tool_use":
        statusLabel.textContent = `Running: ${msg.name}`;
        addActivity("act-tool", "🔧", `${msg.name}(${msg.args ? JSON.stringify(msg.args).slice(0,80) : "…"})`);
        if (msg.name === "run_os_task") startOperatorMode();
        break;

      case "tool_result": {
        const preview = (msg.result || "").slice(0, 100).replace(/\n/g, " ");
        addActivity("act-result", "✓", preview || "(done)");
        if (operatorRunning && msg.name === "run_os_task") stopOperatorMode();
        if (!operatorRunning) statusLabel.textContent = STATE_LABELS.thinking;
        break;
      }

      case "operator_status":
        if (!operatorRunning) startOperatorMode();
        showOperatorStatus(msg.text || "");
        addActivity("act-tool", "🖥", msg.text || "");
        break;

      case "ignored_speaker":
        // Speaker verification rejected this utterance (not the owner)
        addActivity("act-state", "🔒", `Ignored (not you): "${(msg.text || "").slice(0, 40)}"`);
        break;

      case "clear_log":
        // Triggered when you ask Jarvis to clear the chat
        clearChatLog();
        break;

      case "reload":
        // Triggered by Jarvis after editing a frontend file
        setTimeout(() => location.reload(), 300);
        break;

      case "restart_backend":
        // Triggered by Jarvis after editing a backend Python file
        statusLabel.textContent = "Restarting backend…";
        document.body.className = "state-sleeping";
        setTimeout(() => window.electronAPI?.restartBackend(), 600);
        break;
    }
  };

  ws.onclose = () => {
    console.log("[WS] disconnected — retrying in 3 s");
    ws = null;
    statusLabel.textContent = "Reconnecting…";
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = () => { ws.close(); };
}

function sendTrigger() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "trigger" }));
  } else {
    statusLabel.textContent = "Backend offline — check terminal.";
  }
}

// Clear the chat log — called when the backend confirms a "clear_history" voice command
function clearChatLog() {
  commLog.innerHTML = "";
  statusLabel.textContent = "Conversation cleared.";
}

// ── Start ──────────────────────────────────────────────────────────────────────
connect();

// ── Window controls (kept: needed to close a frameless window) ──────────────────
document.getElementById("btn-minimize")?.addEventListener("click", () => window.electronAPI?.minimize());
document.getElementById("btn-close")?.addEventListener("click",    () => window.electronAPI?.close());

// Global hotkey (Ctrl+Shift+J) still works as a manual trigger — not a button
if (window.electronAPI) window.electronAPI.onGlobalTrigger(() => sendTrigger());
