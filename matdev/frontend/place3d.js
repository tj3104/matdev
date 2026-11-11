// ---- 分子を手で置く: 作業台の「分子を置く」で、置く分子を半透明の影として構造の中に出し、つまんで動かす・回す --------------
// 影の位置と向きは入力欄 (x・y・z・rx・ry・rz) と同じものを指す。動かすと欄が変わり、欄を書き換えると影が動く。
// 「適用」で下書きに重ねる (欄の値で MatSim が置く。回す順は x → y → z で、three.js の Euler の "ZYX" と同じ)。

import * as THREE from "./vendor/three.module.min.js";
import {OrbitControls} from "./vendor/OrbitControls.js";
import {atomRadius, disposeGroup, elementColor, structureGroup} from "./structure3d.js";

const ORDER = "ZYX";   // ASE の rotate を x・y・z の順に掛けたもの (Rz·Ry·Rx)
const DEG = 180 / Math.PI;

function round(value, digits) {
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

// 分子の影: 原子を中心 (位置の平均) からの位置で並べた半透明の球
function ghostGroup(molecule) {
  const group = new THREE.Group();
  const n = molecule.positions.length;
  const mean = [0, 1, 2].map((k) => molecule.positions.reduce((sum, p) => sum + p[k], 0) / n);
  const sphere = new THREE.SphereGeometry(1, 16, 12);
  group.userData.local = [];
  molecule.symbols.forEach((symbol, i) => {
    const material = new THREE.MeshStandardMaterial({color: elementColor(symbol), transparent: true, opacity: 0.75,
                                                     emissive: 0x2a6f8a, emissiveIntensity: 0.6});
    const mesh = new THREE.Mesh(sphere, material);
    mesh.scale.setScalar(atomRadius(symbol));
    mesh.position.set(...molecule.positions[i].map((v, k) => v - mean[k]));
    group.userData.local.push(mesh.position.clone());
    group.add(mesh);
  });
  // つかむ所: 分子を包む淡い球 (環の真ん中など、原子のない所でもつかめる)
  const reach = Math.max(...group.userData.local.map((v) => v.length())) + 1.0;
  const handle = new THREE.Mesh(new THREE.SphereGeometry(reach, 24, 16),
                                new THREE.MeshBasicMaterial({color: 0x5cd6ff, transparent: true, opacity: 0.08, depthWrite: false}));
  group.add(handle);
  return group;
}

class Placement {
  constructor(holder, host, form, tip) {
    this.holder = holder;
    this.host = host;
    this.form = form;
    this.tip = tip;
    this.mode = "move";
    this.canvas = document.createElement("canvas");
    holder.replaceChildren(this.canvas);
    this.renderer = new THREE.WebGLRenderer({canvas: this.canvas, antialias: true, alpha: true});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(35, 1, 0.01, 20);
    this.camera.up.set(0, 0, 1);   // 結晶の z 軸を上に。ドラッグと自動回転は z 軸のまわり
    this.camera.position.set(0.9, -1.9, 0.6);
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enablePan = false;
    this.controls.enableDamping = true;
    this.group = structureGroup(host, 1);
    this.inner = this.group.children[0];   // Å の座標 (セルの中心が原点)
    this.center = new THREE.Vector3();
    for (const v of host.cell) this.center.add(new THREE.Vector3(...v).multiplyScalar(0.5));
    this.scene.add(this.group);
    this.hostPoints = host.positions.map((p) => new THREE.Vector3(...p).sub(this.center));
    this.raycaster = new THREE.Raycaster();
    this.listen();
    this.renderer.setAnimationLoop(() => {
      this.resize();
      this.controls.update();
      this.renderer.render(this.scene, this.camera);
    });
  }

  setMode(mode) {   // "move" (動かす) か "rotate" (回す)
    this.mode = mode;
  }

  setMolecule(molecule) {
    if (this.ghost) {
      this.inner.remove(this.ghost);
      disposeGroup(this.ghost);
    }
    this.ghost = ghostGroup(molecule);
    this.inner.add(this.ghost);
    this.fromForm();
  }

  // 入力欄の値 → 影 (位置の空欄はセルの中央)
  fromForm() {
    if (!this.ghost) return;
    const values = this.form.values();
    const at = ["x", "y", "z"].map((k, i) => (values[k] === "" || values[k] === undefined ? this.center.getComponent(i) : values[k]));
    this.ghost.position.set(...at).sub(this.center);
    this.ghost.rotation.set(...["rx", "ry", "rz"].map((k) => (Number(values[k]) || 0) / DEG), ORDER);
    this.report();
  }

  // 影 → 入力欄
  toForm() {
    const at = this.ghost.position.clone().add(this.center);
    const euler = new THREE.Euler().setFromQuaternion(this.ghost.quaternion, ORDER);
    this.form.set({x: round(at.x, 2), y: round(at.y, 2), z: round(at.z, 2),
                   rx: round(euler.x * DEG, 1), ry: round(euler.y * DEG, 1), rz: round(euler.z * DEG, 1)});
    this.report();
  }

  // 元の構造の原子と最も近い距離 (重なりの目安。周期は見ない)
  report() {
    this.ghost.updateMatrix();
    let nearest = Infinity;
    for (const local of this.ghost.userData.local) {
      const point = local.clone().applyMatrix4(this.ghost.matrix);
      for (const host of this.hostPoints) nearest = Math.min(nearest, point.distanceTo(host));
    }
    const close = nearest < 1.2;
    this.tip.className = close ? "notice warn" : "meta";
    this.tip.textContent = Number.isFinite(nearest)
      ? `元の構造の原子との最も近い距離: ${nearest.toFixed(2)} Å${close ? " (近すぎます。重なりを消す距離も使えます)" : ""}`
      : "";
  }

  pointer(event) {
    const rect = this.canvas.getBoundingClientRect();
    return new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  }

  listen() {
    let drag = null;
    this.canvas.addEventListener("pointerdown", (event) => {
      if (!this.ghost) return;
      this.raycaster.setFromCamera(this.pointer(event), this.camera);
      const hit = this.raycaster.intersectObjects(this.ghost.children)[0];
      if (!hit) return;   // 影の外は、ふつうに構造を回す
      this.controls.enabled = false;
      this.canvas.setPointerCapture(event.pointerId);
      const normal = this.camera.getWorldDirection(new THREE.Vector3());
      drag = {id: event.pointerId, plane: new THREE.Plane().setFromNormalAndCoplanarPoint(normal, hit.point), last: hit.point.clone(),
              x: event.clientX, y: event.clientY, rotate: this.mode === "rotate" || event.shiftKey};
      event.preventDefault();
    });
    this.canvas.addEventListener("pointermove", (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      if (drag.rotate) {   // 横に動かすと画面の縦軸、縦に動かすと画面の横軸のまわりに回る
        const up = new THREE.Vector3(0, 1, 0).applyQuaternion(this.camera.quaternion);
        const right = new THREE.Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
        const turn = new THREE.Quaternion().setFromAxisAngle(up, (event.clientX - drag.x) * 0.01)
          .multiply(new THREE.Quaternion().setFromAxisAngle(right, (event.clientY - drag.y) * 0.01));
        this.ghost.quaternion.premultiply(turn);
      } else {   // 画面と平行な面の上で動かす
        this.raycaster.setFromCamera(this.pointer(event), this.camera);
        const point = this.raycaster.ray.intersectPlane(drag.plane, new THREE.Vector3());
        if (!point) return;
        this.ghost.position.add(point.clone().sub(drag.last).divideScalar(this.inner.scale.x));
        drag.last = point;
      }
      drag.x = event.clientX;
      drag.y = event.clientY;
      this.toForm();
    });
    const end = (event) => {
      if (!drag || event.pointerId !== drag.id) return;
      drag = null;
      this.controls.enabled = true;
    };
    this.canvas.addEventListener("pointerup", end);
    this.canvas.addEventListener("pointercancel", end);
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
    disposeGroup(this.group);
    this.controls.dispose();
    this.renderer.dispose();
    this.renderer.forceContextLoss();
  }
}

// 作業台から使う: holder に表示を作り、{setMolecule, fromForm, setMode, dispose} を返す
window.mountPlacement = (holder, host, form, tip) => new Placement(holder, host, form, tip);
