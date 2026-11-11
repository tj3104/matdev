// ---- その場で MD (普段の画面) --------------------------------------------------------------------
// 空間 (VR) を開かずに、ウインドウの中で対話型 MD を動かす。サーバー (/api/imd) と MD の係は空間と同じ。
// 空いている所のドラッグで回し、ホイール・ピンチで寄る。原子の上からのドラッグは、その原子をつまんで画面と平行に動かす。
// 近づけすぎる位置へは係が置かない (blocked のとき、つまんだ原子の輪が赤くなる)。

import * as THREE from "./vendor/three.module.min.js";
import {OrbitControls} from "./vendor/OrbitControls.js";
import {atomRadius, axesTriad, disposeGroup, elementColor} from "./structure3d.js";

const SEND_MS = 50;
const live = {socket: null, renderer: null, scene: null, camera: null, controls: null, group: null, meshes: [], symbols: [],
              center: null, highlight: null, frame: null, ready: null, target: null, engines: [], picked: null,
              fixed: new Set(), drag: null, lastSend: 0, message: ""};

function $id(id) {
  return document.getElementById(id);
}

function send(message) {
  if (live.socket?.readyState === WebSocket.OPEN) live.socket.send(JSON.stringify(message));
}

async function openImd(target) {
  live.target = target;
  const dialog = $id("imd-dialog");
  $id("imd-title").textContent = `その場で MD: ${target.label || ""}`;
  if (!dialog.open) dialog.showModal();
  if (!live.engines.length) {
    live.engines = (await api("imd/engines")).engines;
    $id("imd-engine").replaceChildren(...live.engines.map((e, i) => new Option(e.label, String(i))));
    $id("imd-engine").value = String(Math.max(0, live.engines.findIndex((e) => e.default)));
  }
  setupScene();
  start();
}

function setupScene() {
  if (live.renderer) return;
  const holder = $id("imd-view");
  const canvas = document.createElement("canvas");
  holder.replaceChildren(canvas);
  live.renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true});
  live.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  live.scene = new THREE.Scene();
  live.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 50);
  live.camera.up.set(0, 0, 1);   // 結晶の z 軸を上に。ドラッグで z 軸のまわりに回る
  live.camera.position.set(0.9, -1.9, 0.6);
  live.controls = new OrbitControls(live.camera, canvas);
  live.controls.enableDamping = true;
  live.scene.add(new THREE.AmbientLight(0xffffff, 1.4));
  const light = new THREE.DirectionalLight(0xffffff, 2.2);
  light.position.set(1, 2, 3);
  live.scene.add(light);
  const raycaster = new THREE.Raycaster();
  const pointer = (event) => {
    const rect = canvas.getBoundingClientRect();
    return new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  };
  // 原子の上から押したら、その原子をつまむ (回す操作は止める)
  canvas.addEventListener("pointerdown", (event) => {
    if (!live.group) return;
    raycaster.setFromCamera(pointer(event), live.camera);
    const hit = raycaster.intersectObjects(live.meshes)[0];
    if (!hit || hit.instanceId === undefined) return;
    live.drag = {index: hit.object.userData.indices[hit.instanceId],
                 plane: new THREE.Plane().setFromNormalAndCoplanarPoint(live.camera.getWorldDirection(new THREE.Vector3()), hit.point)};
    live.picked = live.drag.index;
    live.controls.enabled = false;
    canvas.setPointerCapture(event.pointerId);
    showStatus();
  }, {capture: true});
  canvas.addEventListener("pointermove", (event) => {
    if (!live.drag) return;
    const now = performance.now();
    if (now - live.lastSend < SEND_MS) return;
    live.lastSend = now;
    raycaster.setFromCamera(pointer(event), live.camera);
    const point = raycaster.ray.intersectPlane(live.drag.plane, new THREE.Vector3());
    if (!point) return;
    const local = live.inner.worldToLocal(point).add(live.center);   // 原子の座標 (Å) に直す
    send({op: "pull", index: live.drag.index, target: [local.x, local.y, local.z]});
  });
  const release = () => {
    if (!live.drag) return;
    live.drag = null;
    live.controls.enabled = true;
    send({op: "release"});
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  live.renderer.setAnimationLoop(() => {
    const {clientWidth: w, clientHeight: h} = holder;
    if (w && h && (canvas.width !== Math.round(w * live.renderer.getPixelRatio()) || canvas.height !== Math.round(h * live.renderer.getPixelRatio()))) {
      live.renderer.setSize(w, h, false);
      live.camera.aspect = w / h;
      live.camera.updateProjectionMatrix();
    }
    live.controls.update();
    live.renderer.render(live.scene, live.camera);
  });
}

function start() {
  stopSocket();
  clearGroup();
  Object.assign(live, {frame: null, ready: null, picked: null, fixed: new Set(), message: "MD を始めています…"});
  showStatus();
  const engine = live.engines[Number($id("imd-engine").value)] || live.engines[0];
  const socket = new WebSocket(`${location.origin.replace(/^http/, "ws")}/api/imd`);
  live.socket = socket;
  socket.onopen = () => socket.send(JSON.stringify({op: "start", conversation_id: state.conversation, temperature: 300,
    engine: engine.engine, ...(live.target.file ? {file: live.target.file} : {structure_id: live.target.structure_id})}));
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "ready") build(message);
    else if (message.type === "frame") update(message);
    else if (message.type === "saved") live.message = `保存しました: ${message.path} (会話にも残しました)`;
    else if (message.type === "error") live.message = message.message;
    else if (message.type === "closed") live.message = "MD が止まりました";
    if (message.type !== "frame") showStatus();
  };
}

function build(ready) {
  live.ready = ready;
  live.symbols = ready.symbols;
  live.message = "";
  const [a, b, c] = ready.cell.map((v) => new THREE.Vector3(...v));
  live.center = a.clone().add(b).add(c).multiplyScalar(0.5);
  const corners = [];
  for (const i of [0, 1]) for (const j of [0, 1]) for (const k of [0, 1]) {
    corners.push(a.clone().multiplyScalar(i).add(b.clone().multiplyScalar(j)).add(c.clone().multiplyScalar(k)).sub(live.center));
  }
  const extent = Math.max(...corners.map((p) => p.length())) * 2 || 1;
  live.group = new THREE.Group();
  live.inner = new THREE.Group();
  live.inner.scale.setScalar(1 / extent);
  live.group.add(live.inner);
  const byElement = new Map();
  ready.symbols.forEach((symbol, i) => {
    if (!byElement.has(symbol)) byElement.set(symbol, []);
    byElement.get(symbol).push(i);
  });
  const sphere = new THREE.SphereGeometry(1, 16, 12);
  live.meshes = [...byElement].map(([symbol, indices]) => {
    const mesh = new THREE.InstancedMesh(sphere, new THREE.MeshStandardMaterial({color: elementColor(symbol), roughness: 0.35}), indices.length);
    mesh.userData.indices = indices;
    mesh.userData.radius = atomRadius(symbol);
    live.inner.add(mesh);
    return mesh;
  });
  const edges = [[0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3], [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7]];
  const accent = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#5cd6ff";
  live.inner.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(edges.flatMap(([i, j]) => [corners[i], corners[j]])),
                                        new THREE.LineBasicMaterial({color: accent, transparent: true, opacity: 0.6})));
  live.inner.add(axesTriad(corners[0].clone().add(corners[0].clone().normalize().multiplyScalar(extent * 0.14)), extent * 0.26));
  live.highlight = new THREE.Mesh(new THREE.SphereGeometry(1, 20, 14),
                                  new THREE.MeshBasicMaterial({color: accent, wireframe: true, transparent: true, opacity: 0.9}));
  live.highlight.visible = false;
  live.inner.add(live.highlight);
  live.scene.add(live.group);
  update({positions: ready.positions, step: 0, time_ps: 0, temperature: ready.temperature, target_temperature: ready.temperature,
          pulling: null, blocked: false, paused: false, fixed: []});
}

function update(frame) {
  if (!live.group) return;
  const previous = live.frame;
  live.frame = frame;
  const matrix = new THREE.Matrix4();
  for (const mesh of live.meshes) {
    const r = mesh.userData.radius;
    mesh.userData.indices.forEach((atom, k) => {
      const p = frame.positions[atom];
      matrix.makeScale(r, r, r).setPosition(p[0] - live.center.x, p[1] - live.center.y, p[2] - live.center.z);
      mesh.setMatrixAt(k, matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }
  const shown = frame.pulling ?? live.picked;
  live.highlight.visible = shown !== null && shown !== undefined;
  if (live.highlight.visible) {
    const p = frame.positions[shown];
    live.highlight.position.set(p[0] - live.center.x, p[1] - live.center.y, p[2] - live.center.z);
    live.highlight.scale.setScalar(atomRadius(live.symbols[shown]) * 1.5);
    live.highlight.material.color.set(frame.blocked ? "#ff5a5a" : (getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#5cd6ff"));
  }
  if (!previous || frame.step % 5 === 0 || previous.blocked !== frame.blocked || previous.paused !== frame.paused) showStatus();
}

function showStatus() {
  const f = live.frame;
  $id("imd-info").textContent = f
    ? `${live.ready?.engine || ""} / ${live.ready?.n_atoms ?? ""} 原子 / ${f.time_ps ?? 0} ps (${f.step} ステップ) / ${f.temperature} K (目標 ${f.target_temperature} K)`
    : "";
  const status = $id("imd-status");
  status.textContent = f?.blocked ? "これ以上近づけられません (近づきすぎを止めています)"
    : live.message || (f?.paused ? "一時停止中" : "原子の上からドラッグでつまんで動かせます。空いている所のドラッグで回せます");
  status.classList.toggle("error", Boolean(f?.blocked));
  $id("imd-pause").textContent = f?.paused ? "再開" : "一時停止";
  $id("imd-fix").textContent = live.picked !== null && live.fixed.has(live.picked) ? "原子を放す" : "原子を止める";
}

function clearGroup() {
  if (live.group) {
    live.scene.remove(live.group);
    disposeGroup(live.group);
  }
  Object.assign(live, {group: null, inner: null, meshes: [], highlight: null});
}

function stopSocket() {
  if (live.socket) {
    send({op: "stop"});
    live.socket.close();
  }
  live.socket = null;
}

function closeImd() {
  stopSocket();
  clearGroup();
  live.drag = null;
}

// ---- ボタン ---------------------------------------------------------------------------------------
$id("imd-dialog").addEventListener("close", closeImd);
$id("imd-close").addEventListener("click", () => $id("imd-dialog").close());
$id("imd-end").addEventListener("click", () => $id("imd-dialog").close());
$id("imd-engine").addEventListener("change", () => start());   // ポテンシャルを変えたら、元の構造から始め直す
$id("imd-cooler").addEventListener("click", () => send({op: "set", temperature: Math.max(1, (live.frame?.target_temperature ?? 300) - 100)}));
$id("imd-hotter").addEventListener("click", () => send({op: "set", temperature: (live.frame?.target_temperature ?? 300) + 100}));
$id("imd-pause").addEventListener("click", () => send({op: "set", paused: !live.frame?.paused}));
$id("imd-fix").addEventListener("click", () => {
  if (live.picked === null) {
    live.message = "先に原子をつまんでください";
    showStatus();
    return;
  }
  if (live.fixed.has(live.picked)) live.fixed.delete(live.picked); else live.fixed.add(live.picked);
  send({op: "set", fixed: [...live.fixed]});
  showStatus();
});
$id("imd-save").addEventListener("click", () => send({op: "save"}));
$id("imd-space").addEventListener("click", () => {   // 空間 (VR) で続ける: 元の構造から始め直す
  const target = live.target;
  $id("imd-dialog").close();
  window.spaceImd?.(target);
});

window.openImd = openImd;
