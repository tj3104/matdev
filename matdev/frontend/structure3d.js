// ---- 構造の 3D 表示 ---------------------------------------------------------------------------
// 原子を元素の色の球、セルを線で描く。会話のカード・資料・空間で使う。
// 会話のカードは指やマウスで回せる (縦のスクロールは止めない)。WebGL は同時に使える数に限りがあるので、
// 動かす表示は新しい方から MAX_LIVE 個までにし、古いものは「押すと表示」に戻す。

import * as THREE from "./vendor/three.module.min.js";
import {OrbitControls} from "./vendor/OrbitControls.js";

// 色は Jmol の配色、半径は共有結合半径 (Å)
const COLORS = {
  H: 0xffffff, Li: 0xcc80ff, Be: 0xc2ff00, B: 0xffb5b5, C: 0x909090, N: 0x3050f8, O: 0xff0d0d, F: 0x90e050,
  Na: 0xab5cf2, Mg: 0x8aff00, Al: 0xbfa6a6, Si: 0xf0c8a0, P: 0xff8000, S: 0xffff30, Cl: 0x1ff01f, K: 0x8f40d4,
  Ca: 0x3dff00, Ti: 0xbfc2c7, V: 0xa6a6ab, Cr: 0x8a99c7, Mn: 0x9c7ac7, Fe: 0xe06633, Co: 0xf090a0, Ni: 0x50d050,
  Cu: 0xc88033, Zn: 0x7d80b0, Ga: 0xc28f8f, Ge: 0x668f8f, As: 0xbd80e3, Se: 0xffa100, Sr: 0x00ff00, Y: 0x94ffff,
  Zr: 0x94e0e0, Nb: 0x73c2c9, Mo: 0x54b5b5, Pd: 0x006985, Ag: 0xc0c0c0, Cd: 0xffd98f, In: 0xa67573, Sn: 0x668080,
  Sb: 0x9e63b5, Te: 0xd47a00, Ba: 0x00c900, La: 0x70d4ff, Ce: 0xffffc7, Hf: 0x4dc2ff, Ta: 0x4da6ff, W: 0x2194d6,
  Pt: 0xd0d0e0, Au: 0xffd123, Pb: 0x575961, Bi: 0x9e4fb5,
};
const RADII = {
  H: 0.31, Li: 1.28, Be: 0.96, B: 0.84, C: 0.76, N: 0.71, O: 0.66, F: 0.57, Na: 1.66, Mg: 1.41, Al: 1.21, Si: 1.11,
  P: 1.07, S: 1.05, Cl: 1.02, K: 2.03, Ca: 1.76, Ti: 1.6, V: 1.53, Cr: 1.39, Mn: 1.39, Fe: 1.32, Co: 1.26, Ni: 1.24,
  Cu: 1.32, Zn: 1.22, Ga: 1.22, Ge: 1.2, As: 1.19, Se: 1.2, Sr: 1.95, Y: 1.9, Zr: 1.75, Nb: 1.64, Mo: 1.54, Pd: 1.39,
  Ag: 1.45, Cd: 1.44, In: 1.42, Sn: 1.39, Sb: 1.39, Te: 1.38, Ba: 2.15, La: 2.07, Ce: 2.04, Hf: 1.75, Ta: 1.7,
  W: 1.62, Pt: 1.36, Au: 1.36, Pb: 1.46, Bi: 1.48,
};
const ATOM_SCALE = 0.45;
const MAX_LIVE = 4;

export function elementColor(symbol) {
  return COLORS[symbol] ?? 0xb0b0b0;
}

// 表示する球の半径 (Å)
export function atomRadius(symbol) {
  return (RADII[symbol] ?? 1.3) * ATOM_SCALE;
}

function cssColor(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#5cd6ff";
}

// x・y・z の向きの矢印 (赤・緑・青) と文字。構造と一緒に回るよう、構造の中 (Å の座標) に置く。
// at は矢印の根もと、length は矢印の長さ (Å)
const AXIS_COLORS = {x: "#ff5a5a", y: "#5fd38d", z: "#5c9dff"};

function axisLabel(text, colorText, size) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 64;
  const g = canvas.getContext("2d");
  g.font = "bold 48px ui-monospace, monospace";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.lineWidth = 6;
  g.strokeStyle = "rgba(0, 0, 0, 0.65)";   // 明るい背景でも読めるよう縁取り
  g.strokeText(text, 32, 34);
  g.fillStyle = colorText;
  g.fillText(text, 32, 34);
  const texture = new THREE.CanvasTexture(canvas);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({map: texture, depthTest: false, transparent: true}));
  sprite.scale.setScalar(size);
  sprite.renderOrder = 10;
  sprite.raycast = () => {};   // 文字は押せる物にしない (XR の光線では Sprite の当たり判定にカメラが要るため)
  return sprite;
}

export function axesTriad(at, length) {
  const group = new THREE.Group();
  group.position.copy(at);
  const directions = {x: new THREE.Vector3(1, 0, 0), y: new THREE.Vector3(0, 1, 0), z: new THREE.Vector3(0, 0, 1)};
  for (const [name, direction] of Object.entries(directions)) {
    const arrow = new THREE.ArrowHelper(direction, new THREE.Vector3(), length, AXIS_COLORS[name], length * 0.22, length * 0.12);
    group.add(arrow);
    const label = axisLabel(name, AXIS_COLORS[name], length * 0.4);
    label.position.copy(direction.clone().multiplyScalar(length * 1.22));
    group.add(label);
  }
  return group;
}

// セルの原点の角から、セルの外へ少しずらした所に軸の矢印を置く (corners[0] がセルの原点の角)
function cellAxes(corners, extent) {
  const outward = corners[0].clone().normalize();
  const at = corners[0].clone().add(outward.multiplyScalar(extent * 0.14));
  return axesTriad(at, extent * 0.26);
}

// 構造を、最も長い差し渡しが size になる大きさで、中心を原点にした Group にする。
// zUp: 結晶の z 軸を three.js の上 (y) に向ける (空間 (XR) は y が上なので。普段の画面はカメラの上を z にしている)
export function structureGroup(data, size = 1, {zUp = false} = {}) {
  const group = new THREE.Group();
  const [a, b, c] = data.cell.map((v) => new THREE.Vector3(...v));
  const center = a.clone().add(b).add(c).multiplyScalar(0.5);
  const corners = [];
  for (const i of [0, 1]) for (const j of [0, 1]) for (const k of [0, 1]) {
    corners.push(a.clone().multiplyScalar(i).add(b.clone().multiplyScalar(j)).add(c.clone().multiplyScalar(k)).sub(center));
  }
  const extent = Math.max(...corners.map((p) => p.length())) * 2 || 1;
  const scale = size / extent;
  const inner = new THREE.Group();
  inner.scale.setScalar(scale);
  if (zUp) inner.rotation.x = -Math.PI / 2;
  group.add(inner);

  // 元素ごとに 1 つの InstancedMesh にまとめる (原子が多くても軽い)
  const byElement = new Map();
  data.symbols.forEach((symbol, i) => {
    if (!byElement.has(symbol)) byElement.set(symbol, []);
    byElement.get(symbol).push(data.positions[i]);
  });
  const sphere = new THREE.SphereGeometry(1, 20, 14);
  const matrix = new THREE.Matrix4();
  for (const [symbol, positions] of byElement) {
    const material = new THREE.MeshStandardMaterial({color: elementColor(symbol), roughness: 0.35, metalness: 0.15});
    const mesh = new THREE.InstancedMesh(sphere, material, positions.length);
    const radius = (RADII[symbol] ?? 1.3) * ATOM_SCALE;
    positions.forEach((p, i) => {
      matrix.makeScale(radius, radius, radius).setPosition(p[0] - center.x, p[1] - center.y, p[2] - center.z);
      mesh.setMatrixAt(i, matrix);
    });
    inner.add(mesh);
  }

  // セルの 12 本の辺
  const edges = [[0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3], [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7]];
  const points = edges.flatMap(([i, j]) => [corners[i], corners[j]]);
  const lines = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(points),
                                       new THREE.LineBasicMaterial({color: cssColor("--accent"), transparent: true, opacity: 0.8}));
  inner.add(lines);
  inner.add(cellAxes(corners, extent));   // x・y・z の矢印 (構造と一緒に回る)

  group.add(new THREE.AmbientLight(0xffffff, 1.4));
  const light = new THREE.DirectionalLight(0xffffff, 2.2);
  light.position.set(1, 2, 3);
  group.add(light);
  group.userData.legend = [...byElement.keys()];
  return group;
}

export function disposeGroup(group) {
  group.traverse((node) => {
    node.geometry?.dispose();
    if (node.material) [].concat(node.material).forEach((m) => { m.map?.dispose(); m.dispose(); });
  });
}

export async function fetchStructure(app, structureId, repeat = "auto") {
  return api(`views/${encodeURIComponent(app)}/structures/${encodeURIComponent(structureId)}?repeat=${repeat}`);
}

export function legend(symbols) {
  const box = document.createElement("div");
  box.className = "legend";
  for (const symbol of symbols) {
    const chip = document.createElement("span");
    const dot = document.createElement("i");
    dot.style.background = `#${elementColor(symbol).toString(16).padStart(6, "0")}`;
    chip.append(dot, symbol);
    box.append(chip);
  }
  return box;
}

// ---- 会話・資料のカード --------------------------------------------------------------------

const live = [];   // 動いている表示 (新しい順)

class Viewer {
  constructor(holder) {
    this.holder = holder;
    this.canvas = document.createElement("canvas");
    holder.replaceChildren(this.canvas);
    this.renderer = new THREE.WebGLRenderer({canvas: this.canvas, antialias: true, alpha: true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 20);
    this.camera.up.set(0, 0, 1);   // 結晶の z 軸を上に。ドラッグと自動回転は z 軸のまわり
    this.camera.position.set(0.9, -1.9, 0.6);
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableZoom = false;   // ホイールでページのスクロールを奪わない
    this.controls.enablePan = false;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 1.5;
    this.controls.enableDamping = true;
    this.canvas.addEventListener("pointerdown", () => { this.controls.autoRotate = false; });
    this.visible = true;
    this.observer = new IntersectionObserver(([entry]) => { this.visible = entry.isIntersecting; });
    this.observer.observe(holder);
    this.renderer.setAnimationLoop(() => {
      if (!this.visible) return;
      this.resize();
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  show(data) {
    if (this.group) {
      this.scene.remove(this.group);
      disposeGroup(this.group);
    }
    this.group = structureGroup(data, 1);
    this.scene.add(this.group);
  }

  resize() {
    const {clientWidth: w, clientHeight: h} = this.holder;
    if (!w || !h || (w === this.lastW && h === this.lastH)) return;
    this.lastW = w;
    this.lastH = h;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  dispose() {
    this.renderer.setAnimationLoop(null);
    this.observer.disconnect();
    if (this.group) disposeGroup(this.group);
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
  }
}

// 構造のカードを node の中に作る。押すまで WebGL を使わない「眠った」状態にもなる
function mountStructure(node, app, structureId) {
  node.classList.add("structure-card");
  node.replaceChildren();
  const head = Object.assign(document.createElement("div"), {className: "card-head"});
  const title = Object.assign(document.createElement("b"), {textContent: `構造 ${structureId} を読み込み中…`});
  const meta = Object.assign(document.createElement("small"), {textContent: app});
  head.append(title, meta);
  const holder = Object.assign(document.createElement("div"), {className: "structure-view"});
  const tools = Object.assign(document.createElement("div"), {className: "structure-tools"});
  const repeat = document.createElement("select");
  for (const [value, label] of [["auto", "繰り返し: 自動"], ["1,1,1", "1×1×1"], ["2,2,2", "2×2×2"], ["3,3,3", "3×3×3"]]) {
    repeat.append(new Option(label, value));
  }
  const toSpace = Object.assign(document.createElement("button"), {type: "button", className: "quiet small", textContent: "空間で見る"});
  toSpace.addEventListener("click", () => window.openSpaceWith?.(app, structureId));
  tools.append(repeat, toSpace);
  // 表示した構造から、そのまま編集・計算へ (作業台)
  const can = state.apps[app]?.workbench || {};   // state は app.js の画面の状態
  // 計算は、つないだアプリのうちこの構造を受け取れるもの (ezcal なども) から作業台で選ぶ
  const calculable = calcAppsFor({app, structure: structureId, source: "structure"}).length > 0;
  if (app === "matsim") {   // その場で MD を回しながら原子を動かす (空間で)
    const live = Object.assign(document.createElement("button"), {type: "button", className: "small", textContent: "その場で MD"});
    live.addEventListener("click", () => window.openImd?.({structure_id: structureId, label: data?.name || data?.formula}));
    tools.append(live);
  }
  for (const [mode, label] of [["edit", "編集"], ["calculate", "計算"]]) {
    if (mode === "edit" ? !can.edit : !calculable) continue;
    const button = Object.assign(document.createElement("button"), {type: "button", className: "small", textContent: label});
    button.addEventListener("click", () => window.openWorkbench?.(app, structureId, data || {}, mode === "edit" ? "edit" : "calc"));
    tools.append(button);
  }
  const note = Object.assign(document.createElement("small"), {className: "meta"});
  node.append(head, holder, tools, note);

  let viewer = null;
  let data = null;
  const wake = () => {
    if (viewer) return;
    viewer = new Viewer(holder);
    live.unshift({viewer, sleep});
    while (live.length > MAX_LIVE) live.pop().sleep();
    if (data) viewer.show(data);
  };
  const sleep = () => {
    viewer?.dispose();
    viewer = null;
    const wakeButton = Object.assign(document.createElement("button"), {type: "button", className: "quiet", textContent: "押すと 3D で表示"});
    wakeButton.addEventListener("click", wake);
    holder.replaceChildren(wakeButton);
  };
  const load = async () => {
    try {
      data = await fetchStructure(app, structureId, repeat.value);
    } catch (error) {
      title.textContent = `構造 ${structureId}`;
      note.textContent = error.message;
      return;
    }
    title.textContent = `${data.name || data.formula || `構造 ${structureId}`}`;
    const shown = data.shown < data.n_atoms ? ` (${data.shown} 個を表示)` : "";
    note.replaceChildren(legend([...new Set(data.symbols)]),
                         `${data.n_atoms} 原子 / ${data.repeat.join("×")}${shown}・ドラッグで回す`);
    if (viewer) viewer.show(data);
  };
  repeat.addEventListener("change", load);
  wake();
  load();
}

// 作業のファイルから読んだ構造をそのまま出す (アプリを通さない)
function mountStructureData(holder, data) {
  holder.classList.add("structure-view");
  let viewer = new Viewer(holder);
  const entry = {viewer, sleep: () => {
    viewer?.dispose();
    viewer = null;
    const wake = Object.assign(document.createElement("button"), {type: "button", className: "quiet", textContent: "押すと 3D で表示"});
    wake.addEventListener("click", () => mountStructureData(holder, data));
    holder.replaceChildren(wake);
  }};
  live.unshift(entry);
  while (live.length > MAX_LIVE) live.pop().sleep();
  viewer.show(data);
  return legend([...new Set(data.symbols)]);
}

// 計算過程の構造の時間変化: コマ (フレーム) を再生・一時停止・スライダーで送る。data は {symbols, frames: [{index, cell, positions}]}
function mountTrajectory(box, data) {
  const holder = Object.assign(document.createElement("div"), {className: "structure-view"});
  const play = Object.assign(document.createElement("button"), {type: "button", className: "small", textContent: "▶ 再生"});
  const slider = Object.assign(document.createElement("input"), {type: "range", min: 0, max: data.frames.length - 1, value: 0,
                                                                  step: 1, className: "traj-slider"});
  slider.setAttribute("aria-label", "コマ");
  const label = Object.assign(document.createElement("small"), {className: "meta"});
  const tools = Object.assign(document.createElement("div"), {className: "structure-tools"});
  tools.append(play, slider);
  const note = Object.assign(document.createElement("small"), {className: "meta"});
  note.append(legend([...new Set(data.symbols)]), ` ${data.n_atoms} 原子${data.symbols.length < data.n_atoms ? ` (${data.symbols.length} 個を表示)` : ""}` +
              ` / 全 ${data.n_frames_total} コマから ${data.frames.length} コマ・ドラッグで回す`);
  box.append(holder, tools, label, note);
  let viewer = new Viewer(holder);
  viewer.controls.autoRotate = false;
  const entry = {viewer, sleep: () => {
    stop();
    viewer?.dispose();
    viewer = null;
    const wake = Object.assign(document.createElement("button"), {type: "button", className: "quiet", textContent: "押すと 3D で表示"});
    wake.addEventListener("click", () => {
      box.replaceChildren();
      mountTrajectory(box, data);
    });
    holder.replaceChildren(wake);
  }};
  live.unshift(entry);
  while (live.length > MAX_LIVE) live.pop().sleep();
  const show = (i) => {
    const frame = data.frames[i];
    viewer?.show({symbols: data.symbols, positions: frame.positions, cell: frame.cell});
    slider.value = i;
    label.textContent = `コマ ${i + 1} / ${data.frames.length} (トラジェクトリの ${frame.index + 1} 番目)`;
  };
  let timer = null;
  const stop = () => {
    clearInterval(timer);
    timer = null;
    play.textContent = "▶ 再生";
  };
  play.addEventListener("click", () => {
    if (timer) return stop();
    play.textContent = "⏸ 止める";
    timer = setInterval(() => {
      if (!viewer || !box.isConnected) return stop();
      show((Number(slider.value) + 1) % data.frames.length);
    }, 200);
  });
  slider.addEventListener("input", () => {
    stop();
    show(Number(slider.value));
  });
  show(0);
}

window.mountStructure = mountStructure;
window.mountTrajectory = mountTrajectory;
window.mountStructureData = mountStructureData;
// app.js が先に描いたカード (このモジュールの読み込み前) を作る
for (const node of document.querySelectorAll(".structure-card[data-pending]")) {
  delete node.dataset.pending;
  mountStructure(node, node.dataset.app, node.dataset.structure);
}
