// ---- 空間 (XR) ------------------------------------------------------------------------------
// 会話・資料・グラフのパネルを空間に弧状に並べる。スマホ・PC では指やマウスで回して見て、パネルを押して操作する。
// Quest などのブラウザでは「XR で入る」で部屋に重ねて (パススルー) 表示し、コントローラーか手 (つまむ) で同じ操作をする。
// 中身は普段の画面と同じ API から読む (app.js の api・state を使う)。

import * as THREE from "./vendor/three.module.min.js";
import {atomRadius, axesTriad, disposeGroup, elementColor, fetchStructure, structureGroup} from "./structure3d.js";

const PANEL_PX = 1024;          // パネルの横の画素数 (1 m あたり)
const RADIUS = 1.7, HEIGHT = 1.55;
const STRUCTURE_AT = new THREE.Vector3(0, 1.0, -0.85), STRUCTURE_SIZE = 0.6;   // 目の前、胸の高さ
const FOLLOW_SECONDS = 3;   // 新しい構造 (Claude Code の入口で取ってきたものも) を見に行く間隔

const space = {
  open: false, renderer: null, scene: null, camera: null, look: {yaw: 0, pitch: -0.15}, pointers: new Map(),
  panels: [], hits: [],
  data: {items: [], jobs: [], notes: []}, graph: null, lastPointer: null, xrSession: null,
  structure: {list: [], index: 0, entry: null, data: null, group: null, spinning: true, error: null, lastMaterial: null},
  follow: null, mode: "chat", notes: [], note: null, notePage: 0, grab: null, turning: false, bench: null,
  windows: [], files: null, fileDir: "", filePage: 0,
};

function color(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "#5cd6ff";
}

// ---- パネル (キャンバスに描いて板に貼る) ------------------------------------------------------

class Panel {
  constructor({title, width, height, angle, y = HEIGHT, onPress}) {
    this.title = title;
    this.canvas = document.createElement("canvas");
    this.canvas.width = Math.round(PANEL_PX * width);
    this.canvas.height = Math.round(PANEL_PX * height);
    this.texture = new THREE.CanvasTexture(this.canvas);
    this.texture.colorSpace = THREE.SRGBColorSpace;
    this.texture.anisotropy = 4;
    const material = new THREE.MeshBasicMaterial({map: this.texture, transparent: true, side: THREE.DoubleSide});
    this.mesh = new THREE.Mesh(new THREE.PlaneGeometry(width, height), material);
    this.mesh.position.set(Math.sin(angle) * RADIUS, y, -Math.cos(angle) * RADIUS);
    this.mesh.lookAt(0, y, 0);
    this.mesh.userData.panel = this;
    this.regions = [];          // 押せる場所 [{x, y, w, h, action}] (キャンバスの画素)
    this.onPress = onPress;
  }

  begin() {
    const g = this.canvas.getContext("2d");
    const {width: w, height: h} = this.canvas;
    g.clearRect(0, 0, w, h);
    g.fillStyle = color("--panel-solid");
    g.globalAlpha = 0.9;
    g.fillRect(0, 0, w, h);
    g.globalAlpha = 1;
    g.strokeStyle = color("--line-strong");
    g.lineWidth = 3;
    g.strokeRect(1.5, 1.5, w - 3, h - 3);
    g.strokeStyle = color("--accent");
    g.lineWidth = 7;
    const c = 36;
    g.beginPath();
    g.moveTo(4, c); g.lineTo(4, 4); g.lineTo(c, 4);
    g.moveTo(w - 4, h - c); g.lineTo(w - 4, h - 4); g.lineTo(w - c, h - 4);
    g.stroke();
    g.fillStyle = color("--accent");
    g.font = "600 30px ui-monospace, monospace";
    g.textBaseline = "top";
    g.fillText(`◢ ${this.title}`, 28, 24);
    this.regions = [];
    return g;
  }

  end() {
    this.texture.needsUpdate = true;
  }

  press(uv) {
    const x = uv.x * this.canvas.width, y = (1 - uv.y) * this.canvas.height;
    const region = this.regions.find((r) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
    if (region) region.action({x, y});   // 押した位置 (バーなど位置で決めるもの用)
    else this.onPress?.();
  }
}

// 日本語は語の切れ目がないので、1 文字ずつ測って折り返す
function wrap(g, text, width) {
  const lines = [];
  for (const paragraph of String(text).split("\n")) {
    let line = "";
    for (const char of paragraph) {
      if (g.measureText(line + char).width > width && line) {
        lines.push(line);
        line = char;
      } else {
        line += char;
      }
    }
    lines.push(line);
  }
  return lines;
}

function plain(markdown) {
  return markdown.split("\n").filter((l) => !/^\s*\|?\s*-{3,}/.test(l))
    .map((l) => l.replace(/^#+\s*|^[-*+]\s+|[*_`#]/g, "").replace(/\|/g, "  ").trim()).filter(Boolean).join("\n");
}

function button(g, panel, x, y, w, h, label, action, active = false) {
  g.fillStyle = active ? color("--accent") : color("--accent-soft");
  g.fillRect(x, y, w, h);
  g.strokeStyle = color("--line-strong");
  g.lineWidth = 2;
  g.strokeRect(x, y, w, h);
  g.fillStyle = active ? color("--on-accent") : color("--accent");
  g.font = "600 30px system-ui, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(label, x + w / 2, y + h / 2);
  g.textAlign = "left";
  g.textBaseline = "top";
  panel.regions.push({x, y, w, h, action});
}

// 会話: 新しいやり取りを下に詰めて出す。「会話 ▼」で切り替え・新しい会話・削除 (VR でも画面の外に出ずに)
function drawChat(panel) {
  const g = panel.begin();
  const {width: w, height: h} = panel.canvas;
  if (space.chatMenu) {
    drawChatMenu(g, panel);
    panel.end();
    return;
  }
  button(g, panel, w - 260, 14, 228, 52, "会話 ▼", () => openChatMenu());
  g.font = "24px system-ui, sans-serif";
  g.fillStyle = color("--muted");
  g.fillText(`${$("title").textContent}  /  出力先: ${works.active?.name || "なし"}`.slice(0, 44), 32, 76);
  g.font = "34px system-ui, sans-serif";
  const blocks = [];
  for (const item of space.data.items.slice(-12)) {
    const {type, data} = item;
    if (type === "user") blocks.push([color("--accent"), `あなた: ${data.text}`]);
    else if (type === "assistant") blocks.push([color("--fg"), `${state.name}: ${plain(data.text)}`]);
    else if (type === "notice" || type === "confirm") blocks.push([color("--warn"), data.text || `確認: ${data.summary}`]);
  }
  let lines = [];
  for (const [fill, text] of blocks) {
    for (const line of wrap(g, text, w - 64)) lines.push([fill, line]);
    lines.push([fill, ""]);
  }
  const room = Math.floor((h - 240) / 46);
  if (!blocks.length) lines = [[color("--muted"), state.conversation === null ? "会話を選ぶか、話しかけてください" : "まだやり取りはありません"]];
  lines = lines.slice(-room);
  lines.forEach(([fill, line], i) => {
    g.fillStyle = fill;
    g.fillText(line, 32, 120 + i * 46);
  });
  const listening = typeof voice !== "undefined" && voice.listening;
  button(g, panel, 32, h - 100, w - 64, 72, listening ? "■ 聞いています (押すと止める)" : "🎙 話す", () => toggleListening(), listening);
  panel.end();
}

// ---- 会話の切り替え・新しい会話・削除 (空間の会話のパネルで) ---------------------------------------------
const CHAT_ROWS = 8;

async function openChatMenu() {
  space.chatMenu = {mode: "list", page: 0, chats: await api("conversations"), message: ""};
  redraw();
}

function closeChatMenu() {
  space.chatMenu = null;
  redraw();
}

async function chatMenuTry(work) {
  try {
    await work();
  } catch (error) {
    if (space.chatMenu) space.chatMenu.message = error.message;
    else space.data.items = [...space.data.items, {type: "notice", data: {text: error.message}}];
    redraw();
  }
}

// 会話を開く。出力先のない (前からある) 会話は、先に出力先を選んでもらう
function switchChat(chat) {
  if (chat.work_id === null) {
    pickWorkInSpace(`「${chat.title}」の出力先`, async (work) => {
      await api(`conversations/${chat.id}/work`, {method: "PUT", body: JSON.stringify({work_id: work.id})});
      await openChatInSpace(chat.id);
    });
    return;
  }
  chatMenuTry(() => openChatInSpace(chat.id));
}

async function openChatInSpace(id) {
  space.chatMenu = null;
  await openChat(id);
  await loadData();
  refreshHits();
}

// 新しい会話: 出力先 (作業) を選んでから作る
function newChatInSpace() {
  pickWorkInSpace("新しい会話の出力先", async (work) => {
    const {id} = await post("conversations", {work_id: work.id});
    await openChatInSpace(id);
  });
}

async function pickWorkInSpace(title, then) {
  const {works: list} = await api("works");
  space.chatMenu = {mode: "work", page: 0, title, works: list, then, message: ""};
  redraw();
}

function drawChatMenu(g, panel) {
  const {width: w, height: h} = panel.canvas;
  const menu = space.chatMenu;
  g.font = "600 28px system-ui, sans-serif";
  g.fillStyle = color("--fg");
  let rows = [];
  if (menu.mode === "list") {
    g.fillText("会話", 32, 76);
    button(g, panel, w - 360, 14, 328, 52, "＋ 新しい会話", () => chatMenuTry(async () => newChatInSpace()), true);
    rows = menu.chats.map((chat) => ({label: chat.title, active: chat.id === state.conversation,
      pick: () => switchChat(chat), remove: () => { space.chatMenu = {...menu, mode: "delete", chat}; redraw(); }}));
  } else if (menu.mode === "work") {
    g.fillText(menu.title.slice(0, 26), 32, 76);
    const stamp = new Date().toLocaleString("ja-JP", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"});
    button(g, panel, w - 360, 14, 328, 52, "＋ 新しい作業", () => chatMenuTry(async () => {
      const work = await api("works", {method: "POST", body: JSON.stringify({name: `VR の作業 ${stamp}`.replace(/\//g, "-")})});
      await menu.then(work);
    }), true);
    rows = menu.works.map((work) => ({label: `${work.name}`, active: work.id === works.active?.id,
      pick: () => chatMenuTry(() => menu.then(work))}));
  } else if (menu.mode === "delete") {
    g.fillText("会話を消す", 32, 76);
    g.font = "26px system-ui, sans-serif";
    for (const [i, line] of wrap(g, `「${menu.chat.title}」を消します。やり取りの記録を消し、ノートと今月の使用量は残ります。`, w - 64).slice(0, 3).entries()) {
      g.fillText(line, 32, 140 + i * 36);
    }
    const third = (w - 64 - 32) / 3;
    const remove = (materialsToo) => chatMenuTry(async () => {
      await removeChat(menu.chat.id, materialsToo);
      await loadData();
      await openChatMenu();
    });
    button(g, panel, 32, 300, third, 64, "消す", () => remove(false));
    button(g, panel, 48 + third, 300, third, 64, "資料も消す", () => remove(true));
    button(g, panel, 64 + third * 2, 300, third, 64, "やめる", () => { space.chatMenu = {...menu, mode: "list"}; redraw(); });
    if (menu.message) {
      g.fillStyle = "#ff5a5a";
      g.fillText(menu.message.slice(0, 40), 32, 400);
    }
    return;
  }
  const pages = Math.max(1, Math.ceil(rows.length / CHAT_ROWS));
  menu.page = Math.min(menu.page, pages - 1);
  let y = 100;
  if (!rows.length) {
    g.font = "26px system-ui, sans-serif";
    g.fillStyle = color("--muted");
    g.fillText(menu.mode === "list" ? "会話はまだありません" : "作業はまだありません。「＋ 新しい作業」で作れます", 32, y + 40);
  }
  for (const row of rows.slice(menu.page * CHAT_ROWS, (menu.page + 1) * CHAT_ROWS)) {
    button(g, panel, 32, y, row.remove ? w - 64 - 96 : w - 64, 60, row.label.slice(0, 30), row.pick, row.active);
    if (row.remove) button(g, panel, w - 112, y, 80, 60, "✕", row.remove);
    y += 72;
  }
  if (menu.message) {
    g.font = "24px system-ui, sans-serif";
    g.fillStyle = "#ff5a5a";
    g.fillText(menu.message.slice(0, 44), 32, h - 110);
  }
  if (pages > 1) {
    button(g, panel, 32, h - 88, 100, 64, "◀", () => { menu.page = (menu.page + pages - 1) % pages; redraw(); });
    button(g, panel, 148, h - 88, 100, 64, "▶", () => { menu.page = (menu.page + 1) % pages; redraw(); });
  }
  button(g, panel, w - 232, h - 88, 200, 64, menu.mode === "work" ? "やめる" : "閉じる", () => closeChatMenu());
}

// 資料: この会話のジョブとノート。押すとグラフを出す
// 資料: 上の切り替えで「この会話」(ジョブ) と「ノート」(過去のノートすべて)
function drawJobs(panel) {
  const g = panel.begin();
  const {width: w} = panel.canvas;
  if (space.mode === "bench") {
    drawBench(g, panel);
    panel.end();
    return;
  }
  const tabW = (w - 64 - 24) / 3;
  button(g, panel, 32, 76, tabW, 56, "この会話", () => setMaterialsMode("chat"), space.mode === "chat");
  button(g, panel, 32 + tabW + 12, 76, tabW, 56, "ノート", () => setMaterialsMode("notes"), space.mode === "notes");
  button(g, panel, 32 + (tabW + 12) * 2, 76, tabW, 56, "ファイル", () => setMaterialsMode("files"), space.mode === "files");
  if (space.mode === "notes") {
    drawNotes(g, panel);
    panel.end();
    return;
  }
  if (space.mode === "files") {
    drawFiles(g, panel);
    panel.end();
    return;
  }
  let y = 150;
  const entries = [];
  for (const job of space.data.jobs) entries.push({app: job.app, job_id: job.job_id, label: `${job.app} / ジョブ ${job.job_id}`, sub: job.tools.join(", ")});
  for (const note of space.data.notes) {
    for (const jobId of note.job_ids) {
      if (!entries.some((e) => e.app === note.app && String(e.job_id) === String(jobId))) {
        entries.push({app: note.app, job_id: String(jobId), label: `${note.app} / ジョブ ${jobId}`, sub: `ノート: ${note.title}`});
      }
    }
  }
  if (!entries.length) {
    g.fillStyle = color("--muted");
    g.font = "28px system-ui, sans-serif";
    for (const [i, line] of wrap(g, "この会話にはまだジョブがありません。会話を選ぶか、計算を頼むとここに並びます", w - 64).entries()) g.fillText(line, 32, y + i * 38);
  }
  for (const entry of entries.slice(0, 8)) {
    const active = space.graph && space.graph.app === entry.app && String(space.graph.jobId) === String(entry.job_id);
    const graphs = state.apps[entry.app]?.graphs;
    button(g, panel, 32, y, w - 64, 64, entry.label + (graphs ? "" : " (グラフなし)"), () => graphs && openGraph(entry.app, entry.job_id), active);
    g.fillStyle = color("--muted");
    g.font = "22px ui-monospace, monospace";
    g.fillText(entry.sub.slice(0, 60), 40, y + 70);
    y += 112;
  }
  panel.end();
}

const NOTES_PER_PAGE = 7;

// ノート: 一覧 (◀ ▶ でめくる) と、押したノートの中身 (分かったこと・ジョブのグラフ・構造)
function drawNotes(g, panel) {
  const {width: w, height: h} = panel.canvas;
  const notes = space.notes;
  if (space.note) {
    const note = space.note;
    g.fillStyle = color("--fg");
    g.font = "600 30px system-ui, sans-serif";
    const titleLines = wrap(g, note.title, w - 64).slice(0, 2);
    titleLines.forEach((line, i) => g.fillText(line, 32, 150 + i * 38));
    let y = 150 + titleLines.length * 38 + 8;
    g.font = "22px ui-monospace, monospace";
    g.fillStyle = color("--muted");
    g.fillText(`${note.created_at.slice(0, 10)} / ${note.app || ""}`, 32, y);
    y += 40;
    g.font = "26px system-ui, sans-serif";
    g.fillStyle = color("--accent");
    g.fillText("分かったこと", 32, y);
    y += 36;
    g.fillStyle = color("--fg");
    const buttons = [];
    for (const jobId of note.job_ids) {
      if (state.apps[note.app]?.graphs) buttons.push([`ジョブ ${jobId} のグラフ`, () => openGraph(note.app, String(jobId))]);
    }
    for (const structureId of note.structure_ids || []) {
      if (state.apps[note.app]?.structures) {
        buttons.push([`構造 ${structureId} を出す`, () => showStructure({app: note.app, structure_id: String(structureId)})]);
      }
    }
    const room = Math.floor((h - y - 100 - buttons.length * 76) / 34);
    const lines = wrap(g, plain(note.findings), w - 64);
    lines.slice(0, Math.max(1, room)).forEach((line, i) => g.fillText(line, 32, y + i * 34));
    if (lines.length > room) {
      g.fillStyle = color("--muted");
      g.fillText("… 続きは画面のノートで", 32, y + room * 34);
    }
    let by = h - 100 - buttons.length * 76;
    for (const [label, action] of buttons) {
      button(g, panel, 32, by, w - 64, 64, label, action);
      by += 76;
    }
    button(g, panel, 32, h - 92, w - 64, 64, "◀ 一覧に戻る", () => { space.note = null; redraw(); });
    return;
  }
  if (!notes.length) {
    g.fillStyle = color("--muted");
    g.font = "28px system-ui, sans-serif";
    g.fillText("ノートはまだありません", 32, 160);
    return;
  }
  const pages = Math.ceil(notes.length / NOTES_PER_PAGE);
  space.notePage = Math.min(space.notePage, pages - 1);
  let y = 150;
  for (const note of notes.slice(space.notePage * NOTES_PER_PAGE, (space.notePage + 1) * NOTES_PER_PAGE)) {
    g.font = "26px system-ui, sans-serif";
    const title = wrap(g, note.title, w - 96)[0];
    button(g, panel, 32, y, w - 64, 60, title, () => { space.note = note; redraw(); });
    g.fillStyle = color("--muted");
    g.font = "20px ui-monospace, monospace";
    const refs = [note.job_ids.length ? `ジョブ ${note.job_ids.join(",")}` : "", (note.structure_ids || []).length ? `構造 ${note.structure_ids.join(",")}` : ""];
    g.fillText(`${note.created_at.slice(0, 10)}  ${refs.filter(Boolean).join(" / ")}`.slice(0, 60), 40, y + 64);
    y += 104;
  }
  if (pages > 1) {
    button(g, panel, 32, h - 92, 120, 64, "◀", () => { space.notePage = (space.notePage + pages - 1) % pages; redraw(); });
    button(g, panel, w - 152, h - 92, 120, 64, "▶", () => { space.notePage = (space.notePage + 1) % pages; redraw(); });
    g.fillStyle = color("--muted");
    g.font = "24px ui-monospace, monospace";
    g.textAlign = "center";
    g.fillText(`${space.notePage + 1} / ${pages}`, w / 2, h - 72);
    g.textAlign = "left";
  }
}

async function setMaterialsMode(mode) {
  space.mode = mode;
  space.note = null;
  if (mode === "notes") space.notes = await api("notes").catch(() => []);
  if (mode === "files") await loadFiles(space.fileDir);
  redraw();
}

// ---- 作業のファイルとウインドウ -------------------------------------------------------------------
// 資料の「ファイル」で作業のディレクトリをたどり、押したファイルをウインドウで開く。ウインドウは自分のまわりに並べる。
// 開いているウインドウは、次の依頼で [画面で開いているもの] としてジャービスに伝わる (files.js の viewingNow)。

const FILES_PER_PAGE = 8;
const SLOTS = [1.3, -1.35, 1.95, -2.0, 2.6, -2.65];   // ウインドウを置く向き (正面が 0、右が正)
const ICONS = {dir: "[ ]", structure: "原子", table: "表", image: "画像", html: "図", text: "文", other: "・"};

async function loadFiles(directory) {
  space.fileDir = directory || "";
  space.filePage = 0;
  space.files = works.active ? await api(`works/${works.active.id}/files?directory=${encodeURIComponent(space.fileDir)}`).catch(() => null) : null;
}

function drawFiles(g, panel) {
  const {width: w, height: h} = panel.canvas;
  g.font = "26px system-ui, sans-serif";
  if (!works.active) {
    g.fillStyle = color("--muted");
    for (const [i, line] of wrap(g, "作業が決まっていません。普段の画面の左下の「作業」で選ぶか作ってください", w - 64).entries()) g.fillText(line, 32, 160 + i * 36);
    return;
  }
  g.fillStyle = color("--accent");
  g.fillText(`${works.active.name} / ${space.fileDir}`.slice(0, 34), 32, 148);
  const data = space.files;
  if (!data) {
    g.fillStyle = color("--muted");
    g.fillText("読み込み中…", 32, 200);
    return;
  }
  let y = 196;
  if (space.fileDir) {
    const parent = space.fileDir.split("/").slice(0, -1).join("/");
    button(g, panel, 32, y, w - 64, 56, "↑ 上のディレクトリ", () => loadFiles(parent).then(redraw));
    y += 66;
  }
  const pages = Math.max(1, Math.ceil(data.entries.length / FILES_PER_PAGE));
  space.filePage = Math.min(space.filePage, pages - 1);
  if (!data.entries.length) {
    g.fillStyle = color("--muted");
    g.fillText("まだ何もありません", 32, y + 20);
  }
  for (const entry of data.entries.slice(space.filePage * FILES_PER_PAGE, (space.filePage + 1) * FILES_PER_PAGE)) {
    const opened = space.windows.some((win) => win.path === entry.path);
    button(g, panel, 32, y, w - 64, 60, `${ICONS[entry.kind] || "・"}  ${entry.name}`.slice(0, 40),
           () => (entry.dir ? loadFiles(entry.path).then(redraw) : openSpaceWindow(entry.path)), opened);
    y += 70;
  }
  if (pages > 1) {
    button(g, panel, 32, h - 92, 120, 64, "◀", () => { space.filePage = (space.filePage + pages - 1) % pages; redraw(); });
    button(g, panel, w - 152, h - 92, 120, 64, "▶", () => { space.filePage = (space.filePage + 1) % pages; redraw(); });
    g.fillStyle = color("--muted");
    g.textAlign = "center";
    g.fillText(`${space.filePage + 1} / ${pages}`, w / 2, h - 72);
    g.textAlign = "left";
  }
}

async function openSpaceWindow(path) {
  const already = space.windows.find((win) => win.path === path);
  if (already) return;
  const used = new Set(space.windows.map((win) => win.slot));
  const slot = SLOTS.findIndex((_, i) => !used.has(i));
  if (slot < 0) {
    closeSpaceWindow(space.windows[space.windows.length - 1]);   // いっぱいなら一番古いものを閉じる
    return openSpaceWindow(path);
  }
  const angle = SLOTS[slot];
  const win = {path, slot, file: null, error: null, page: 0, x: 0, y: 1, image: null, group: null};
  win.panel = new Panel({title: path.split("/").pop(), width: 0.95, height: 0.8, angle, y: HEIGHT - 0.05});
  win.panel.mesh.userData.window = win;
  space.scene.add(win.panel.mesh);
  space.windows.unshift(win);
  refreshHits();
  drawWindow(win);
  redraw();
  try {
    win.file = await api(`works/${works.active.id}/file?path=${encodeURIComponent(path)}`);
    if (win.file.kind === "structure") {   // 構造はウインドウの手前に浮かべる
      win.group = structureGroup(win.file.structure, 0.55, {zUp: true});
      win.group.position.set(Math.sin(angle) * (RADIUS - 0.45), HEIGHT - 0.1, -Math.cos(angle) * (RADIUS - 0.45));
      space.scene.add(win.group);
      refreshHits();
    } else if (win.file.kind === "image") {
      win.image = new Image();
      win.image.onload = () => drawWindow(win);
      win.image.src = `/api/works/${works.active.id}/image?path=${encodeURIComponent(path)}`;
    }
  } catch (error) {
    win.error = error.message;
  }
  drawWindow(win);
}

function closeSpaceWindow(win) {
  space.scene.remove(win.panel.mesh);
  win.panel.texture.dispose();
  if (win.group) {
    space.scene.remove(win.group);
    disposeGroup(win.group);
  }
  space.windows = space.windows.filter((w) => w !== win);
  refreshHits();
  redraw();
}

function refreshHits() {
  space.hits = [...space.panels.map((p) => p.mesh), ...(space.structure.group && !imd.active ? [space.structure.group] : []),
                ...(imd.group ? [imd.group] : []),
                ...space.windows.flatMap((win) => [win.panel.mesh, ...(win.group ? [win.group] : [])])];
}

function drawWindow(win) {
  const panel = win.panel;
  const g = panel.begin();
  const {width: w, height: h} = panel.canvas;
  button(g, panel, w - 100, 16, 76, 52, "✕", () => closeSpaceWindow(win));
  g.font = "24px system-ui, sans-serif";
  g.fillStyle = color("--muted");
  g.fillText(win.path.slice(-46), 32, 72);
  const file = win.file;
  if (win.error || !file) {
    g.fillStyle = win.error ? color("--bad") : color("--muted");
    g.fillText(win.error || "読み込み中…", 32, 120);
    panel.end();
    return;
  }
  if (file.kind === "table") {
    const names = Object.keys(file.table.columns);
    win.x = Math.min(win.x, names.length - 1);
    win.y = Math.min(win.y, names.length - 1);
    const axis = (y, label, key) => {
      button(g, panel, 32, y, 64, 52, "◀", () => { win[key] = (win[key] + names.length - 1) % names.length; drawWindow(win); });
      button(g, panel, w - 96, y, 64, 52, "▶", () => { win[key] = (win[key] + 1) % names.length; drawWindow(win); });
      g.fillStyle = color("--fg");
      g.textAlign = "center";
      g.fillText(`${label}: ${names[win[key]]}`, w / 2, y + 12);
      g.textAlign = "left";
    };
    axis(104, "横軸", "x");
    axis(164, "縦軸", "y");
    plot(g, {x: 110, y: 240, w: w - 150, h: h - 320}, file.table.columns[names[win.x]], file.table.columns[names[win.y]], names[win.x]);
  } else if (file.kind === "text") {
    g.font = "22px ui-monospace, monospace";
    const lines = wrap(g, file.text, w - 64);
    const per = Math.floor((h - 190) / 30);
    const pages = Math.max(1, Math.ceil(lines.length / per));
    g.fillStyle = color("--fg");
    lines.slice(win.page * per, (win.page + 1) * per).forEach((line, i) => g.fillText(line, 32, 104 + i * 30));
    if (pages > 1) {
      button(g, panel, 32, h - 76, 100, 56, "◀", () => { win.page = (win.page + pages - 1) % pages; drawWindow(win); });
      button(g, panel, w - 132, h - 76, 100, 56, "▶", () => { win.page = (win.page + 1) % pages; drawWindow(win); });
      g.fillStyle = color("--muted");
      g.textAlign = "center";
      g.fillText(`${win.page + 1} / ${pages}`, w / 2, h - 60);
      g.textAlign = "left";
    }
  } else if (file.kind === "image" && win.image?.complete && win.image.naturalWidth) {
    const box = {w: w - 64, h: h - 130};
    const scale = Math.min(box.w / win.image.naturalWidth, box.h / win.image.naturalHeight);
    const iw = win.image.naturalWidth * scale, ih = win.image.naturalHeight * scale;
    g.fillStyle = "#fff";
    g.fillRect(32 + (box.w - iw) / 2, 104, iw, ih);
    g.drawImage(win.image, 32 + (box.w - iw) / 2, 104, iw, ih);
  } else if (file.kind === "structure") {
    const st = file.structure;
    g.fillStyle = color("--fg");
    g.font = "600 30px system-ui, sans-serif";
    g.fillText(`${st.formula} / ${st.n_atoms} 原子`, 32, 116);
    g.font = "24px system-ui, sans-serif";
    g.fillStyle = color("--muted");
    g.fillText("手前に浮かんでいる構造を掴むと回せます", 32, 164);
    const apps = calcAppsFor({source: "file", structure: win.path});   // 構造ファイルを受けるアプリ (ezcal など)
    if (apps.length) {
      button(g, panel, 32, h - 100, 280, 64, "計算", () => {
        startBench("calc", {app: apps[0], structure: win.path, symbols: [...new Set(st.symbols)]})
          .catch((error) => { win.error = error.message; drawWindow(win); });
      });
    }
    button(g, panel, 330, h - 100, 280, 64, "その場で MD", () => startImd({file: win.path, label: win.path.split("/").pop()}));
  } else if (file.kind === "html") {
    g.fillText("回せる図 (Plotly) は普段の画面で開いてください", 32, 120);
    g.fillText("同じ図の png・表 (csv) は空間でも開けます", 32, 160);
  } else {
    g.fillText("この種類のファイルは開けません", 32, 120);
  }
  panel.end();
}

// ---- 対話型 MD -------------------------------------------------------------------------------------
// サーバー (/api/imd) と WebSocket でつなぎ、MD の係が返す原子の位置で目の前の構造を描き替える (1 秒に約 25 回)。
// 原子をつまむと、その原子は手の方へ少しずつ動く。ほかの原子に近づきすぎる位置には係が置かない (blocked で赤く知らせる)。

const IMD_SIZE = 0.8, SEND_MS = 50;
const imd = {active: false, socket: null, group: null, inner: null, meshes: [], symbols: [], center: null, frame: null,
             ready: null, engines: [], engine: 0, target: null, message: "", lastSend: 0, picked: null, fixed: new Set(),
             highlight: null};

function imdSend(message) {
  if (imd.socket?.readyState === WebSocket.OPEN) imd.socket.send(JSON.stringify(message));
}

// engine を省くと既定のポテンシャル (NEP89 の CPU)
async function startImd(target, engine = null) {
  stopImd();
  if (!imd.engines.length) imd.engines = (await api("imd/engines")).engines;
  imd.engine = engine ?? Math.max(0, imd.engines.findIndex((e) => e.default));
  Object.assign(imd, {active: true, target, message: "MD を始めています…", frame: null, ready: null, picked: null, fixed: new Set()});
  redraw();
  const socket = new WebSocket(`${location.origin.replace(/^http/, "ws")}/api/imd`);
  imd.socket = socket;
  socket.onopen = () => socket.send(JSON.stringify({op: "start", conversation_id: state.conversation, temperature: 300,
    engine: imd.engines[imd.engine].engine, ...(target.file ? {file: target.file} : {structure_id: target.structure_id})}));
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "ready") buildImd(message);
    else if (message.type === "frame") updateImd(message);
    else if (message.type === "saved") imd.message = `保存しました: ${message.path}`;
    else if (message.type === "error") imd.message = message.message;
    else if (message.type === "closed") imd.message = "MD が止まりました";
    if (message.type !== "frame") redraw();
  };
  socket.onclose = () => {
    if (imd.socket === socket && imd.active) {
      imd.message = imd.message || "接続が切れました";
      redraw();
    }
  };
}

function stopImd() {
  if (imd.socket) {
    imdSend({op: "stop"});
    imd.socket.close();
  }
  if (imd.group) {
    space.scene.remove(imd.group);
    disposeGroup(imd.group);
  }
  Object.assign(imd, {active: false, socket: null, group: null, inner: null, meshes: [], highlight: null, frame: null});
  if (space.structure.group) space.structure.group.visible = true;
  space.dragAtom = space.xrDrag = null;
  if (space.scene) refreshHits();
}

function buildImd(ready) {
  imd.ready = ready;
  imd.symbols = ready.symbols;
  imd.message = "";
  const [a, b, c] = ready.cell.map((v) => new THREE.Vector3(...v));
  imd.center = a.clone().add(b).add(c).multiplyScalar(0.5);
  const extent = Math.max(a.length(), b.length(), c.length(), a.clone().add(b).add(c).length()) || 1;
  imd.group = new THREE.Group();
  imd.group.position.copy(STRUCTURE_AT);
  imd.inner = new THREE.Group();
  imd.inner.scale.setScalar(IMD_SIZE / extent);
  imd.inner.rotation.x = -Math.PI / 2;   // 結晶の z 軸を上に (回すと z 軸のまわり)。引っぱる先は worldToLocal で原子の座標に戻す
  imd.group.add(imd.inner);
  const sphere = new THREE.SphereGeometry(1, 16, 12);
  const byElement = new Map();
  ready.symbols.forEach((symbol, i) => {
    if (!byElement.has(symbol)) byElement.set(symbol, []);
    byElement.get(symbol).push(i);
  });
  imd.meshes = [...byElement].map(([symbol, indices]) => {
    const mesh = new THREE.InstancedMesh(sphere, new THREE.MeshStandardMaterial({color: elementColor(symbol), roughness: 0.35}),
                                         indices.length);
    mesh.userData.imdIndices = indices;
    mesh.userData.radius = atomRadius(symbol);
    imd.inner.add(mesh);
    return mesh;
  });
  // セルの枠
  const corners = [];
  for (const i of [0, 1]) for (const j of [0, 1]) for (const k of [0, 1]) {
    corners.push(a.clone().multiplyScalar(i).add(b.clone().multiplyScalar(j)).add(c.clone().multiplyScalar(k)).sub(imd.center));
  }
  const edges = [[0, 1], [0, 2], [0, 4], [1, 3], [1, 5], [2, 3], [2, 6], [3, 7], [4, 5], [4, 6], [5, 7], [6, 7]];
  imd.inner.add(new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(edges.flatMap(([i, j]) => [corners[i], corners[j]])),
                                       new THREE.LineBasicMaterial({color: color("--accent"), transparent: true, opacity: 0.6})));
  // x・y・z の矢印 (セルの原点の角から少し外に。構造と一緒に回る)
  imd.inner.add(axesTriad(corners[0].clone().add(corners[0].clone().normalize().multiplyScalar(extent * 0.14)), extent * 0.26));
  // つまんでいる原子の印 (水色、近づけすぎで赤)
  imd.highlight = new THREE.Mesh(new THREE.SphereGeometry(1, 20, 14),
                                 new THREE.MeshBasicMaterial({color: color("--accent"), wireframe: true, transparent: true, opacity: 0.9}));
  imd.highlight.visible = false;
  imd.inner.add(imd.highlight);
  imd.group.add(new THREE.AmbientLight(0xffffff, 1.4));
  const light = new THREE.DirectionalLight(0xffffff, 2.2);
  light.position.set(1, 2, 3);
  imd.group.add(light);
  space.scene.add(imd.group);
  if (space.structure.group) space.structure.group.visible = false;
  updateImd({positions: ready.positions, step: 0, temperature: ready.temperature, target_temperature: ready.temperature,
             pulling: null, blocked: false, paused: false, fixed: []});
  refreshHits();
}

function updateImd(frame) {
  if (!imd.group) return;
  const previous = imd.frame;
  imd.frame = frame;
  const matrix = new THREE.Matrix4();
  for (const mesh of imd.meshes) {
    const r = mesh.userData.radius;
    mesh.userData.imdIndices.forEach((atom, k) => {
      const p = frame.positions[atom];
      matrix.makeScale(r, r, r).setPosition(p[0] - imd.center.x, p[1] - imd.center.y, p[2] - imd.center.z);
      mesh.setMatrixAt(k, matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
    mesh.computeBoundingSphere();
  }
  const shown = frame.pulling ?? imd.picked;
  imd.highlight.visible = shown !== null && shown !== undefined;
  if (imd.highlight.visible) {
    const p = frame.positions[shown];
    const r = atomRadius(imd.symbols[shown]) * 1.5;
    imd.highlight.position.set(p[0] - imd.center.x, p[1] - imd.center.y, p[2] - imd.center.z);
    imd.highlight.scale.setScalar(r);
    imd.highlight.material.color.set(frame.blocked ? "#ff5a5a" : color("--accent"));
  }
  // 札は 5 回に 1 回だけ描き直す (毎回は重い)。状態が変わったときはすぐ
  if (!previous || frame.step % 5 === 0 || previous.blocked !== frame.blocked || previous.paused !== frame.paused) {
    drawStructure(space.panels[3]);
  }
}

// 当たった物が対話型 MD の原子なら、その原子の番号
function imdAtom(hit) {
  if (!hit || hit.instanceId === undefined || !hit.object.userData.imdIndices) return null;
  return hit.object.userData.imdIndices[hit.instanceId];
}

// 空間の点 (世界の座標) へ原子を引っぱる。原子の座標 (Å) に直して送る
function imdPull(index, worldPoint) {
  const now = performance.now();
  if (now - imd.lastSend < SEND_MS || !imd.inner) return;
  imd.lastSend = now;
  imd.picked = index;
  const local = imd.inner.worldToLocal(worldPoint.clone()).add(imd.center);
  imdSend({op: "pull", index, target: [local.x, local.y, local.z]});
}

function drawImd(panel) {
  const g = panel.begin();
  const {width: w, height: h} = panel.canvas;
  const f = imd.frame;
  g.font = "600 28px system-ui, sans-serif";
  g.fillStyle = color("--fg");
  g.fillText(`対話型 MD: ${imd.target?.label || ""}`.slice(0, 30), 32, 70);
  g.font = "22px ui-monospace, monospace";
  g.fillStyle = color("--muted");
  const engine = imd.ready?.engine || imd.engines[imd.engine]?.label || "";
  g.fillText(`${engine}`.slice(0, 40), 32, 108);
  if (f) {
    g.fillText(`${imd.ready?.n_atoms ?? ""} 原子 / ${f.time_ps ?? 0} ps (${f.step} ステップ) / ${f.temperature} K (目標 ${f.target_temperature} K)`.slice(0, 52), 32, 140);
  }
  g.font = "24px system-ui, sans-serif";
  g.fillStyle = f?.blocked ? "#ff5a5a" : color("--accent");
  const status = f?.blocked ? "これ以上近づけられません (近づきすぎを止めています)"
    : imd.message || (f?.paused ? "一時停止中" : "原子をつまんで動かせます。グリップで握ると回せます");
  for (const [i, line] of wrap(g, status, w - 64).slice(0, 2).entries()) g.fillText(line, 32, 176 + i * 30);
  const third = (w - 64 - 32) / 3;
  const set = (message) => imdSend({op: "set", ...message});
  const target = f?.target_temperature ?? 300;
  button(g, panel, 32, h - 248, third, 64, "−100 K", () => set({temperature: Math.max(1, target - 100)}));
  button(g, panel, 32 + third + 16, h - 248, third, 64, f?.paused ? "再開" : "一時停止", () => set({paused: !f?.paused}));
  button(g, panel, 32 + (third + 16) * 2, h - 248, third, 64, "+100 K", () => set({temperature: target + 100}));
  const picked = imd.picked;
  const fixedNow = picked !== null && imd.fixed.has(picked);
  button(g, panel, 32, h - 168, third, 64, fixedNow ? "原子を放す" : "原子を止める", () => {
    if (picked === null) {
      imd.message = "先に原子をつまんでください";
      redraw();
      return;
    }
    if (fixedNow) imd.fixed.delete(picked); else imd.fixed.add(picked);
    set({fixed: [...imd.fixed]});
  });
  button(g, panel, 32 + third + 16, h - 168, third, 64, "保存", () => imdSend({op: "save"}), true);
  button(g, panel, 32 + (third + 16) * 2, h - 168, third, 64, "終了", () => { stopImd(); redraw(); });
  button(g, panel, 32, h - 88, w - 64, 64, `ポテンシャル: ${(imd.engines[imd.engine]?.label || "").slice(0, 24)} ▶`, () => {
    // 切り替えると、元の構造から始め直す
    startImd(imd.target, (imd.engine + 1) % Math.max(1, imd.engines.length)).catch((error) => { imd.message = error.message; redraw(); });
  });
  panel.end();
}

// 普段の画面の「その場で MD」から
window.spaceImd = async (target) => {
  await openSpace();
  await startImd(target);
};

// 普段の画面の「空間に出す」から
window.spaceOpenFile = async (path) => {
  await openSpace();
  await openSpaceWindow(path);
};

// 次の依頼に添える空間のウインドウ
window.spaceViewing = () => space.windows.filter((win) => win.file).map((win) => {
  const file = win.file;
  if (file.kind === "table") {
    const names = Object.keys(file.table.columns);
    return `空間のグラフ ${win.path} (横軸 ${names[win.x]}・縦軸 ${names[win.y]})`;
  }
  if (file.kind === "structure") return `空間の構造 ${win.path} (${file.structure.formula}, ${file.structure.n_atoms} 原子)`;
  return `空間の${file.kind === "image" ? "画像" : "文字"} ${win.path}`;
});

// ---- 空間の作業台 (編集・計算) -------------------------------------------------------------------
// 資料のパネルを作業台に切り替える。文字を打たずに済むよう、選ぶ・◀ ▶ で増減する・押して切り替えるだけで進める。
// 文字の欄 (方位など) は既定値のまま。細かい指定は声で頼むか、普段の画面の作業台で。

const BENCH_ROWS = 7, BENCH_LIST = 8;

function benchApi(action, args = {}, extra = {}) {
  const bench = space.bench;
  return api(`workbench/${encodeURIComponent(bench.app)}`,
             {method: "POST", body: JSON.stringify({action, args, conversation_id: state.conversation, ...extra})});
}

// target を省くと目の前の構造 (アプリの構造)。作業の構造ファイルなら {app, structure: パス, symbols}
async function startBench(mode, target = null) {
  const st = space.structure;
  if (!target) {
    if (!st.entry) throw new Error("先に構造を出してください");
    target = {app: st.entry.app, structure: st.entry.structure_id, symbols: [...new Set(st.data?.symbols || [])]};
  }
  const source = state.apps[target.app]?.workbench?.source || "structure";
  const origin = {app: target.app, structure: source === "file" ? String(target.structure) : Number(target.structure), source};
  // 計算は、つないだアプリのうちこの構造を受け取れるもの (ezcal なども) から選ぶ
  const calcApps = mode === "calc" ? calcAppsFor(origin) : [target.app];
  if (!calcApps.length) throw new Error("この構造で計算できるアプリがありません");
  space.bench = {mode, origin, calcApps, app: null, wb: {}, structure: origin.structure, symbols: target.symbols || [],
                 step: "pick", page: 0, list: [], chosen: null, fields: [], values: {}, fieldPage: 0, result: "", busy: false,
                 picker: null, potentials: []};
  space.mode = "bench";
  redraw();
  await switchBenchApp(calcApps.includes(target.app) ? target.app : calcApps[0]);
}

// 作業台で使うアプリを切り替えて、一覧を読み直す (構造ファイルを受けるアプリには作業の cif を渡す)
async function switchBenchApp(app) {
  const bench = space.bench;
  const wb = state.apps[app]?.workbench || {};
  let structure = bench.origin.structure;
  if (wb.source === "file" && bench.origin.source === "structure") structure = await structureFileFor(bench.origin.app, structure);
  Object.assign(bench, {app, wb, structure, step: "pick", page: 0, list: [], chosen: null, result: ""});
  redraw();
  if (bench.mode === "edit") {
    bench.list = (await benchApi("edit_list")).result.ops.filter((op) => op.needs_structure)
      .map((op) => ({key: op.key, label: op.label, group: op.group, note: op.note, fields: op.fields}));
    if (wb.draft) {   // 下書き: 編集を重ねて、最後に 1 回だけ保存する
      bench.draft = (await benchApi("draft_start", {structure_id: bench.origin.structure})).result;
      showDraft(bench.draft);
    }
  } else {
    bench.list = (await benchApi("calc_list")).result.map((k) => ({key: k.key, label: k.label, group: k.group, note: k.note}));
    bench.potentials = wb.potentials ? (await benchApi("potentials")).result.filter((p) => p.available !== false) : [];
  }
  redraw();
}

// 入力欄: 文字を打つもの・ファイル・詳しい設定は出さず、既定値を使う
function benchFields(fields, symbols) {
  return (fields || []).filter((f) => !f.advanced && f.type !== "file").map((f) => {
    if (f.type === "species") return {...f, choices: elementChoices(f, symbols), default: f.default ?? elementChoices(f, symbols)[0]};
    return f;
  });
}

async function chooseBench(item) {
  const bench = space.bench;
  bench.chosen = item;
  bench.result = "";
  bench.fieldPage = 0;
  if (bench.mode === "edit") {
    bench.fields = benchFields(item.fields, bench.symbols);
  } else {
    const described = (await benchApi("calc_describe", {[bench.wb.kind_arg]: item.key})).result;
    const potential = bench.potentials.length ? [{key: "__potential", label: "ポテンシャル", type: "select",
                       options: bench.potentials.map((p) => ({value: p.id, label: p.name})), default: bench.potentials[0]?.id}] : [];
    if (!described.fields && described.stages_example) {   // MD: 段階ごとにアンサンブル・ステップ・温度
      bench.fields = [...potential, ...described.stages_example.flatMap((stage, i) => [
        {key: `__s${i}_ensemble`, label: `段階 ${i + 1} アンサンブル`, type: "select", default: stage.ensemble,
         options: ["nvt", "npt", "nve"].map((e) => ({value: e, label: e.toUpperCase()}))},
        {key: `__s${i}_steps`, label: `段階 ${i + 1} ステップ`, type: "int", default: stage.steps, min: 100},
        {key: `__s${i}_temperature`, label: `段階 ${i + 1} 温度`, type: "number", default: stage.temperature, min: 1, unit: "K"},
        ...(stage.temperature_end !== undefined ? [{key: `__s${i}_temperature_end`, label: `段階 ${i + 1} 終わりの温度`, type: "number",
                                                   default: stage.temperature_end, min: 1, unit: "K"}] : []),
      ])];
      bench.stageCount = described.stages_example.length;
      bench.stagePressure = described.stages_example.map((stage) => stage.pressure);
    } else {
      bench.fields = [...potential, ...benchFields(described.fields, bench.symbols)];
      bench.stageCount = 0;
    }
  }
  bench.values = Object.fromEntries(bench.fields.map((f) => [f.key, f.default]));
  bench.step = "form";
  redraw();
}

function niceStep(value) {
  const size = Math.abs(value) * 0.1;
  if (!size) return 0.1;
  const power = 10 ** Math.floor(Math.log10(size));
  return [1, 2, 5, 10].map((m) => m * power).find((s) => s >= size * 0.99);
}

function adjust(field, direction) {
  const bench = space.bench;
  const value = bench.values[field.key];
  if (field.type === "bool") {
    bench.values[field.key] = !value;
  } else if (field.type === "select") {
    const options = field.options || [];
    const at = Math.max(0, options.findIndex((o) => o.value === value));
    bench.values[field.key] = options[(at + direction + options.length) % options.length]?.value;
  } else if (field.type === "int" || field.type === "number") {
    const step = field.type === "int" ? Math.max(1, Math.round(niceStep(value || 1))) : niceStep(value);
    let next = Number(((value || 0) + direction * step).toPrecision(6));
    if (field.min !== undefined && field.min !== null) next = Math.max(field.min, next);
    if (field.max !== undefined && field.max !== null) next = Math.min(field.max, next);
    bench.values[field.key] = next;
  }
  bench.result = "";
  redraw();
}

function benchArgs() {
  const bench = space.bench;
  const shown = bench.fields.filter((f) => visible(f, bench.values));
  const options = {};
  for (const f of shown) if (!f.key.startsWith("__") && f.type !== "text") options[f.key] = bench.values[f.key];
  if (bench.mode === "edit") return {op: bench.chosen.key, structure_id: bench.structure, options};
  const args = {[bench.wb.kind_arg]: bench.chosen.key, [bench.wb.structure_arg]: bench.structure};
  if (bench.potentials.length) args.potential_id = Number(bench.values.__potential);
  if (bench.stageCount) {
    args.stages = Array.from({length: bench.stageCount}, (_, i) => {
      const stage = {ensemble: bench.values[`__s${i}_ensemble`], steps: bench.values[`__s${i}_steps`],
                     temperature: bench.values[`__s${i}_temperature`]};
      if (bench.values[`__s${i}_temperature_end`] !== undefined) stage.temperature_end = bench.values[`__s${i}_temperature_end`];
      if (stage.ensemble === "npt") stage.pressure = bench.stagePressure[i] ?? 0;
      return stage;
    });
    if (bench.wb.traj_arg) args[bench.wb.traj_arg] = trajInterval(args.stages, "");   // 全体で約 50 コマ (構造の時間変化)
  } else {
    args.options = options;
  }
  return args;
}

async function benchPreview() {
  const bench = space.bench;
  bench.result = "確かめています…";
  redraw();
  try {
    if (bench.mode === "edit") {
      const {result: data} = await benchApi("edit_preview", benchArgs());
      bench.result = `${data.summary || ""} → ${data.formula} (${data.n_atoms} 原子)${(data.warnings || []).map((w) => `\n注意: ${w}`).join("")}`;
    } else {
      const {result: data} = await benchApi("calc_preview", benchArgs());
      const time = typeof data.estimated_seconds === "number"
        ? ` / ${data.total_steps} ステップ / 目安 ${data.estimated_seconds < 60 ? "1 分未満" : `${(data.estimated_seconds / 60).toFixed(1)} 分`}`
        : " / 所要時間の目安なし (投入は会話の確認のカードで)";
      bench.result = `${data.formula ?? ""} ${data.n_atoms ?? data.natoms ?? "?"} 原子${time}` +
                     (data.notes || []).map((n) => `\n注意: ${n}`).join("");
    }
  } catch (error) {
    bench.result = error.message;
  }
  redraw();
}

// 下書きの形を目の前に出す (元の構造の代わりに)
function showDraft(state) {
  const st = space.structure;
  if (st.group) {
    space.scene.remove(st.group);
    disposeGroup(st.group);
  }
  st.data = {...state.view, name: `下書き (${state.step} 手)`, formula: state.formula, n_atoms: state.n_atoms, repeat: [1, 1, 1]};
  st.group = structureGroup(st.data, STRUCTURE_SIZE, {zUp: true});
  st.group.position.copy(STRUCTURE_AT);
  space.scene.add(st.group);
  refreshHits();
}

async function draftApply() {
  const bench = space.bench;
  if (bench.busy) return;
  bench.busy = true;
  bench.result = "下書きに重ねています…";
  redraw();
  try {
    const args = benchArgs();
    const state = (await benchApi("draft_apply", {draft: bench.draft.draft, op: args.op, options: args.options})).result;
    bench.draft = state;
    showDraft(state);
    bench.result = `重ねました (${state.step} 手): ${state.formula} / ${state.n_atoms} 原子${(state.warnings || []).map((w) => `\n注意: ${w}`).join("")}`;
  } catch (error) {
    bench.result = error.message;
  } finally {
    bench.busy = false;
    redraw();
  }
}

async function draftUndo() {
  const bench = space.bench;
  try {
    bench.draft = (await benchApi("draft_undo", {draft: bench.draft.draft})).result;
    showDraft(bench.draft);
    bench.result = `一つ前に戻しました (${bench.draft.step} 手)`;
  } catch (error) {
    bench.result = error.message;
  }
  redraw();
}

async function draftSave() {
  const bench = space.bench;
  if (bench.busy || !bench.draft?.step) return;
  bench.busy = true;
  bench.result = "保存しています…";
  redraw();
  try {
    const conversation = await benchConversation();
    const labels = bench.draft.steps.map((step) => step.label).join(" → ");
    const data = await benchApi("draft_save", {draft: bench.draft.draft, name: ""}, {conversation_id: conversation, label: labels});
    bench.result = data.summary;
    bench.draft = null;
    followStructures().catch(() => {});   // 保存した構造を目の前に出す
  } catch (error) {
    bench.result = error.message;
  } finally {
    bench.busy = false;
    redraw();
  }
}

async function benchCommit() {
  const bench = space.bench;
  if (bench.busy) return;
  bench.busy = true;
  bench.result = bench.mode === "edit" ? "保存しています…" : "投入しています… 長い計算は会話の確認のカードで「実行する」を押してください";
  redraw();
  try {
    const conversation = await benchConversation();
    const action = bench.mode === "edit" ? "edit_save" : "calc_submit";
    const args = bench.mode === "edit" ? {...benchArgs(), name: ""} : benchArgs();
    const data = await benchApi(action, args, {conversation_id: conversation, label: bench.chosen.label});
    bench.result = data.summary;
    followStructures().catch(() => {});   // 保存した構造を目の前に出す
  } catch (error) {
    bench.result = error.message;
  } finally {
    bench.busy = false;
    redraw();
  }
}

function drawBench(g, panel) {
  const {width: w, height: h} = panel.canvas;
  const bench = space.bench;
  const title = bench.mode === "edit" ? "編集" : "計算";
  g.fillStyle = color("--accent");
  g.font = "600 30px system-ui, sans-serif";
  g.fillText(`作業台: ${title}${bench.chosen && bench.step === "form" ? ` / ${bench.chosen.label}` : ""}`, 32, 84);
  button(g, panel, w - 112, 72, 80, 56, "✕", () => {
    const hadDraft = Boolean(bench.draft);
    space.mode = "chat";
    space.bench = null;
    if (hadDraft && space.structure.entry) showStructure(space.structure.entry).catch(() => {});   // 下書きをやめて元の構造に戻す
    redraw();
  });
  g.font = "26px system-ui, sans-serif";
  if (bench.step === "pick") {
    if (!bench.list.length) {
      g.fillStyle = color("--muted");
      g.fillText("読み込み中…", 32, 170);
      return;
    }
    const pages = Math.ceil(bench.list.length / BENCH_LIST);
    let y = 150;
    if (bench.calcApps.length > 1) {   // 計算に使うアプリを切り替える
      const width = (w - 64 - 12 * (bench.calcApps.length - 1)) / bench.calcApps.length;
      bench.calcApps.forEach((app, i) => button(g, panel, 32 + i * (width + 12), y, width, 56, app,
        () => switchBenchApp(app).catch((error) => { bench.result = error.message; redraw(); }), app === bench.app));
      y += 76;
    }
    for (const item of bench.list.slice(bench.page * BENCH_LIST, (bench.page + 1) * BENCH_LIST)) {
      button(g, panel, 32, y, w - 64, 64, `${item.label}  (${item.group || ""})`,
             () => chooseBench(item).catch((error) => { bench.result = error.message; redraw(); }));
      y += 84;
    }
    if (pages > 1) {
      button(g, panel, 32, h - 92, 120, 64, "◀", () => { bench.page = (bench.page + pages - 1) % pages; redraw(); });
      button(g, panel, w - 152, h - 92, 120, 64, "▶", () => { bench.page = (bench.page + 1) % pages; redraw(); });
      g.fillStyle = color("--muted");
      g.textAlign = "center";
      g.fillText(`${bench.page + 1} / ${pages}`, w / 2, h - 72);
      g.textAlign = "left";
    }
    return;
  }
  if (bench.picker) {
    drawPeriodic(g, panel, bench.picker);
    return;
  }
  // 入力欄: ラベル / 値 / ◀ ▶ (元素は周期表)
  const shown = bench.fields.filter((f) => visible(f, bench.values));
  const pages = Math.max(1, Math.ceil(shown.length / BENCH_ROWS));
  bench.fieldPage = Math.min(bench.fieldPage, pages - 1);
  let y = 140;
  for (const field of shown.slice(bench.fieldPage * BENCH_ROWS, (bench.fieldPage + 1) * BENCH_ROWS)) {
    g.fillStyle = color("--muted");
    g.font = "22px system-ui, sans-serif";
    g.fillText(field.label.slice(0, 22), 32, y);
    const value = bench.values[field.key];
    const shownValue = field.type === "bool" ? (value ? "する" : "しない")
      : field.type === "species" ? (value === "*" ? "すべて" : value)
      : field.type === "select" ? ((field.options || []).find((o) => o.value === value)?.label ?? String(value))
      : `${value ?? ""}${field.unit ? ` ${field.unit}` : ""}`;
    g.fillStyle = color("--fg");
    g.font = "600 26px system-ui, sans-serif";
    g.fillText(String(shownValue).slice(0, 24), 32, y + 30);
    if (field.type === "species") {
      button(g, panel, w - 200, y + 4, 168, 56, "周期表", () => { bench.picker = field; redraw(); });
    } else if (field.type !== "text") {
      button(g, panel, w - 200, y + 4, 76, 56, "◀", () => adjust(field, -1));
      button(g, panel, w - 108, y + 4, 76, 56, "▶", () => adjust(field, 1));
    }
    y += 82;
  }
  if (pages > 1) {
    button(g, panel, 32, y, (w - 80) / 2, 48, "◀ 前の欄", () => { bench.fieldPage = (bench.fieldPage + pages - 1) % pages; redraw(); });
    button(g, panel, 48 + (w - 80) / 2, y, (w - 80) / 2, 48, "次の欄 ▶", () => { bench.fieldPage = (bench.fieldPage + 1) % pages; redraw(); });
    y += 60;
  }
  if (bench.result) {
    g.fillStyle = color("--accent");
    g.font = "24px system-ui, sans-serif";
    wrap(g, bench.result, w - 64).slice(0, 4).forEach((line, i) => g.fillText(line, 32, y + 6 + i * 32));
  }
  const half = (w - 64 - 16) / 2;
  if (bench.mode === "edit" && bench.draft) {   // 下書き: 適用で重ね、戻す、最後に保存
    const third = (w - 64 - 32) / 3;
    button(g, panel, 32, h - 168, third, 64, "適用", () => draftApply());
    button(g, panel, 48 + third, h - 168, third, 64, `戻す (${bench.draft.step} 手)`, () => draftUndo());
    button(g, panel, 64 + third * 2, h - 168, third, 64, "保存", () => draftSave(), true);
  } else {
    button(g, panel, 32, h - 168, half, 64, bench.mode === "edit" ? "プレビュー" : "見積もり", () => benchPreview());
    button(g, panel, 48 + half, h - 168, half, 64, bench.mode === "edit" ? "保存して出す" : "投入", () => benchCommit(), true);
  }
  button(g, panel, 32, h - 92, w - 64, 64, "◀ 選び直す", () => { bench.step = "pick"; bench.result = ""; bench.picker = null; redraw(); });
}

// 周期表を描いて 1 つ選ぶ。構造にある元素は光らせ、選べない元素は暗くする
function drawPeriodic(g, panel, field) {
  const {width: w, height: h} = panel.canvas;
  const bench = space.bench;
  const structureOnly = field.source === "structure";
  g.fillStyle = color("--fg");
  g.font = "600 26px system-ui, sans-serif";
  g.fillText(`${field.label}: 周期表から選ぶ`, 32, 140);
  const cell = Math.floor((w - 64) / 18);
  const top = 190;
  for (const {symbol, row, col} of PERIODIC) {
    const x = 32 + (col - 1) * cell, y = top + (row - 1) * cell + (row >= 8 ? cell * 0.4 : 0);
    const enabled = !structureOnly || field.choices.includes(symbol);
    const inStructure = bench.symbols.includes(symbol);
    const current = bench.values[field.key] === symbol;
    g.globalAlpha = enabled ? 1 : 0.25;
    g.fillStyle = current ? color("--accent") : inStructure ? color("--accent-soft") : "rgba(0,0,0,0)";
    g.fillRect(x + 1, y + 1, cell - 2, cell - 2);
    g.strokeStyle = inStructure || current ? color("--accent") : color("--line-strong");
    g.lineWidth = inStructure ? 3 : 1;
    g.strokeRect(x + 1, y + 1, cell - 2, cell - 2);
    g.fillStyle = current ? color("--on-accent") : inStructure ? color("--accent") : color("--fg");
    g.font = `600 ${Math.round(cell * 0.42)}px ui-monospace, monospace`;
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(symbol, x + cell / 2, y + cell / 2);
    g.textAlign = "left";
    g.textBaseline = "top";
    g.globalAlpha = 1;
    if (enabled) panel.regions.push({x, y, w: cell, h: cell, action: () => pickFromTable(field, symbol)});
  }
  let y = top + cell * 9.6;
  g.fillStyle = color("--muted");
  g.font = "22px system-ui, sans-serif";
  g.fillText(structureOnly ? "構造にある元素から選びます" : "光っているのは構造にある元素です", 32, y);
  y += 44;
  if (field.allow_all) button(g, panel, 32, y, w - 64, 60, "すべて", () => pickFromTable(field, "*"), bench.values[field.key] === "*");
  button(g, panel, 32, h - 92, w - 64, 64, "◀ 戻る", () => { bench.picker = null; redraw(); });
}

function pickFromTable(field, symbol) {
  const bench = space.bench;
  bench.values[field.key] = symbol;
  bench.picker = null;
  bench.result = "";
  redraw();
}

// グラフ: 「表示」「縦軸」を押すと名前の一覧が出て、押したものを描く。表 (csv) と図 (ezcal の plots/*.png など) を並べる
const MENU_ROWS = 8;

function drawGraph(panel) {
  const g = panel.begin();
  const {width: w, height: h} = panel.canvas;
  const graph = space.graph;
  g.font = "28px system-ui, sans-serif";
  if (!graph) {
    g.fillStyle = color("--muted");
    g.fillText("資料のジョブを押すと、ここにグラフが出ます", 32, 90);
    panel.end();
    return;
  }
  g.fillStyle = color("--fg");
  g.fillText(`${graph.app} / ジョブ ${graph.jobId}`, 32, 72);
  if (graph.error || !graph.items.length) {
    g.fillStyle = graph.error ? color("--bad") : color("--muted");
    g.fillText(graph.error || "読み込み中…", 32, 130);
    panel.end();
    return;
  }
  const item = graph.items[graph.index];
  const table = item.kind === "table" ? graph.table : null;
  const xName = table ? (graph.xName && graph.xName in table.columns ? graph.xName : xColumn(item.dataset, table)) : null;
  const ys = table ? yChoices(item.dataset, table, xName) : [];
  const labels = item.kind === "table" ? Object.fromEntries(item.dataset.columns.map((c) => [c.key, c.label])) : {};
  if (graph.menu) {
    const close = () => { graph.menu = null; redraw(); };
    drawGraphMenu(g, panel, graph.menu === "items"
      ? graph.items.map((it, i) => ({label: it.title, active: i === graph.index, pick: () => chooseGraph(i)}))
      : graph.menu === "x"
        ? Object.keys(table.columns).map((name) => ({label: labels[name] || name, active: name === xName, pick: () => {
            graph.xName = name;
            const next = yChoices(item.dataset, table, name);   // 横軸を変えたら、縦軸は選び直せる範囲に
            graph.yIndex = Math.min(graph.yIndex, Math.max(0, next.length - 1));
            close();
          }}))
        : ys.map((y, i) => ({label: y.label, active: i === graph.yIndex, pick: () => { graph.yIndex = i; close(); }})));
    panel.end();
    return;
  }
  const menuWidth = graph.final ? w - 64 - 306 : w - 64;
  button(g, panel, 32, 100, menuWidth, 60, `表示: ${item.title}  ▼`.slice(0, graph.final ? 26 : 40),
         () => { graph.menu = "items"; graph.menuPage = 0; redraw(); });
  if (graph.final) button(g, panel, w - 32 - 290, 100, 290, 60, graph.saving || "最終構造を利用する", () => useFinal());
  if (item.kind === "image") {
    drawGraphImage(g, graph, {x: 32, y: 180, w: w - 64, h: h - 210});
    panel.end();
    return;
  }
  if (item.kind === "trajectory") {
    drawMotion(g, panel, {x: 32, y: 180, w: w - 64, h: h - 210});
    panel.end();
    return;
  }
  if (!graph.table) {
    g.fillStyle = color("--muted");
    g.fillText("読み込み中…", 32, 220);
    panel.end();
    return;
  }
  const choice = ys[graph.yIndex % Math.max(1, ys.length)];
  const half = (w - 64 - 16) / 2;
  button(g, panel, 32, 172, half, 56, `横軸: ${labels[xName] || xName}  ▼`.slice(0, 22),
         () => { graph.menu = "x"; graph.menuPage = 0; redraw(); });
  button(g, panel, 48 + half, 172, half, 56, `縦軸: ${choice?.label || "なし"}  ▼`.slice(0, 22),
         () => { graph.menu = "y"; graph.menuPage = 0; redraw(); });
  plot(g, {x: 110, y: 270, w: w - 150, h: h - 360}, table.columns[xName] || [],
       (choice?.lines || []).map((n) => table.columns[n] || []), labels[xName] || xName);
  panel.end();
}

// 縦軸の選択肢: 線が複数ある表 (バンド・部分 DOS) は「すべて」を先頭に
function yChoices(dataset, table, xName = null) {
  const names = Object.keys(table.columns);
  xName = xName ?? xColumn(dataset, table);
  const labels = Object.fromEntries(dataset.columns.map((c) => [c.key, c.label]));
  const several = dataset.y.filter((n) => names.includes(n) && n !== xName);
  return [...(several.length > 1 ? [{label: `すべて (${several.length} 本)`, lines: several}] : []),
          ...names.filter((n) => n !== xName).map((n) => ({label: labels[n] || n, lines: [n]}))];
}

function drawGraphMenu(g, panel, choices) {
  const {width: w, height: h} = panel.canvas;
  const graph = space.graph;
  const pages = Math.max(1, Math.ceil(choices.length / MENU_ROWS));
  graph.menuPage = Math.min(graph.menuPage || 0, pages - 1);
  let y = 100;
  for (const choice of choices.slice(graph.menuPage * MENU_ROWS, (graph.menuPage + 1) * MENU_ROWS)) {
    button(g, panel, 32, y, w - 64, 52, choice.label.slice(0, 40), choice.pick, choice.active);
    y += 60;
  }
  const bottom = h - 76;
  if (pages > 1) {
    button(g, panel, 32, bottom, 100, 56, "◀", () => { graph.menuPage = (graph.menuPage + pages - 1) % pages; redraw(); });
    button(g, panel, 148, bottom, 100, 56, "▶", () => { graph.menuPage = (graph.menuPage + 1) % pages; redraw(); });
  }
  button(g, panel, w - 232, bottom, 200, 56, "閉じる", () => { graph.menu = null; redraw(); });
}

function drawGraphImage(g, graph, box) {
  const image = graph.image;
  if (!image?.complete || !image.naturalWidth) {
    g.fillStyle = color("--muted");
    g.fillText(graph.imageError || "読み込み中…", box.x, box.y + 40);
    return;
  }
  const scale = Math.min(box.w / image.naturalWidth, box.h / image.naturalHeight);
  const iw = image.naturalWidth * scale, ih = image.naturalHeight * scale;
  const x = box.x + (box.w - iw) / 2;
  g.fillStyle = "#fff";
  g.fillRect(x, box.y, iw, ih);
  g.drawImage(image, x, box.y, iw, ih);
}

const ALL_LINES = "__all__";
const LINE_COLORS = ["#5cd6ff", "#f2a541", "#9b8cff", "#5fd38d", "#ff7aa8", "#c8d85c"];

// 折れ線 (ys は 1 本か複数本)。線はぼかさずにくっきり描く
function plot(g, box, xs, ys, xLabel) {
  const series = (Array.isArray(ys[0]) ? ys : [ys]).map((column) => {
    const points = [];
    for (let i = 0; i < Math.min(xs.length, column.length); i++) if (xs[i] !== null && column[i] !== null) points.push([xs[i], column[i]]);
    return points;
  }).filter((points) => points.length);
  const points = series.flat();
  g.strokeStyle = color("--line-strong");
  g.lineWidth = 2;
  g.strokeRect(box.x, box.y, box.w, box.h);
  if (!points.length) return;
  let [x0, x1] = [Math.min(...points.map((p) => p[0])), Math.max(...points.map((p) => p[0]))];
  let [y0, y1] = [Math.min(...points.map((p) => p[1])), Math.max(...points.map((p) => p[1]))];
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * 0.05;
  y0 -= pad; y1 += pad;
  const sx = (v) => box.x + (v - x0) / (x1 - x0) * box.w;
  const sy = (v) => box.y + box.h - (v - y0) / (y1 - y0) * box.h;
  g.font = "22px ui-monospace, monospace";
  g.fillStyle = color("--muted");
  g.strokeStyle = color("--line");
  g.lineWidth = 1;
  for (const v of ticks(y0, y1, 5)) {
    g.beginPath(); g.moveTo(box.x, sy(v)); g.lineTo(box.x + box.w, sy(v)); g.stroke();
    g.textAlign = "right"; g.fillText(format(v), box.x - 8, sy(v) - 10);
  }
  for (const v of ticks(x0, x1, 5)) {
    g.beginPath(); g.moveTo(sx(v), box.y); g.lineTo(sx(v), box.y + box.h); g.stroke();
    g.textAlign = "center"; g.fillText(format(v), sx(v), box.y + box.h + 10);
  }
  g.fillStyle = color("--fg");
  g.fillText(xLabel, box.x + box.w / 2, box.y + box.h + 44);
  g.textAlign = "left";
  const many = series.length > LINE_COLORS.length;   // バンドなど線が多いときは同じ色で細く
  series.forEach((line, k) => {
    g.strokeStyle = many || series.length === 1 ? color("--accent") : LINE_COLORS[k];
    g.lineWidth = many ? 1.5 : 3;
    g.beginPath();
    line.forEach((p, i) => (i ? g.lineTo(sx(p[0]), sy(p[1])) : g.moveTo(sx(p[0]), sy(p[1]))));
    g.stroke();
  });
}

// 構造の札: 名前・原子数・元素の色と、切り替え・回転のボタン。立体は目の前に浮かべる
function drawStructure(panel) {
  if (imd.active) {
    drawImd(panel);
    return;
  }
  const g = panel.begin();
  const {width: w, height: h} = panel.canvas;
  const st = space.structure;
  g.font = "28px system-ui, sans-serif";
  if (!st.entry) {
    g.fillStyle = color("--muted");
    for (const [i, line] of wrap(g, "構造を取ってくると、目の前に立体で出ます (「MP から Ti の構造を持ってきて」など)", w - 64).entries()) {
      g.fillText(line, 32, 80 + i * 38);
    }
    panel.end();
    return;
  }
  g.fillStyle = st.error ? color("--bad") : color("--fg");
  const title = st.error || (st.data ? `${st.data.name || st.data.formula}` : "読み込み中…");
  g.fillText(title.slice(0, 32), 32, 72);
  if (st.data) {
    g.font = "24px ui-monospace, monospace";
    g.fillStyle = color("--muted");
    g.fillText(`${st.data.n_atoms} 原子 / ${st.data.repeat.join("×")}`, 32, 114);
    let x = 32;
    for (const symbol of [...new Set(st.data.symbols)].slice(0, 8)) {
      g.fillStyle = `#${elementColor(symbol).toString(16).padStart(6, "0")}`;
      g.beginPath(); g.arc(x + 10, 162, 10, 0, Math.PI * 2); g.fill();
      g.fillStyle = color("--fg");
      g.fillText(symbol, x + 26, 150);
      x += 40 + g.measureText(symbol).width;
    }
  }
  const third = (w - 64 - 32) / 3;
  button(g, panel, 32, h - 168, third, 64, "◀ 前", () => pickStructure(1));
  button(g, panel, 32 + third + 16, h - 168, third, 64, st.spinning ? "回転を止める" : "回す", () => { st.spinning = !st.spinning; drawStructure(panel); });
  button(g, panel, 32 + (third + 16) * 2, h - 168, third, 64, "次 ▶", () => pickStructure(-1));
  if (space.motion) {   // 時間変化の再生中は、編集・計算・その場で MD を出さない (元の構造とは別のものを見ているため)
    panel.end();
    return;
  }
  // 編集・計算 (作業台)。XR の中ではフォームを出せないので、声で頼んでもらう
  const can = state.apps[st.entry.app]?.workbench || {};
  const open = (mode) => startBench(mode).catch((error) => { st.error = error.message; redraw(); });
  if (can.edit) button(g, panel, 32, h - 88, third, 64, "編集", () => open("edit"));
  const calculable = calcAppsFor({app: st.entry.app, structure: st.entry.structure_id, source: "structure"}).length > 0;
  if (calculable) button(g, panel, 32 + third + 16, h - 88, third, 64, "計算", () => open("calc"));
  if (st.entry.app === "matsim") {   // その場で MD を回しながら原子を動かす
    button(g, panel, 32 + (third + 16) * 2, h - 88, third, 64, "その場で MD",
           () => startImd({structure_id: st.entry.structure_id, label: st.data?.name || st.data?.formula}));
  }
  panel.end();
}

// ---- 構造 -----------------------------------------------------------------------------------

async function followStructures() {
  const query = state.conversation === null ? "" : `?conversation_id=${state.conversation}`;
  const list = await api(`structures/latest${query}`);
  const st = space.structure;
  st.list = list;
  if (list.length && list[0].material_id !== st.lastMaterial) {   // 新しく取ってきた (触れた) 構造を出す
    st.lastMaterial = list[0].material_id;
    await showStructure(list[0]);
  } else {
    redraw();
  }
}

function pickStructure(step) {
  const st = space.structure;
  if (!st.list.length) return;
  const at = st.list.findIndex((e) => e.app === st.entry?.app && String(e.structure_id) === String(st.entry?.structure_id));
  const next = st.list[(Math.max(0, at) + step + st.list.length) % st.list.length];
  showStructure(next).catch(() => {});
}

async function showStructure(entry) {
  stopMotion();
  const st = space.structure;
  st.entry = entry;
  st.data = null;
  st.error = null;
  redraw();
  try {
    st.data = await fetchStructure(entry.app, entry.structure_id);
  } catch (error) {
    st.error = error.message;
    redraw();
    return;
  }
  if (st.group) {
    space.scene.remove(st.group);
    disposeGroup(st.group);
  }
  st.group = structureGroup(st.data, STRUCTURE_SIZE, {zUp: true});
  st.group.position.copy(STRUCTURE_AT);
  space.scene.add(st.group);
  refreshHits();
  redraw();
}

// 会話のカードの「空間で見る」から
window.openSpaceWith = async (app, structureId) => {
  await openSpace();
  await showStructure({app, structure_id: String(structureId)});
};

// 計算の結果の「構造の時間変化」の「空間で見る」から: グラフのパネルで時間変化を選び、目の前で再生する
window.openSpaceMotion = async (app, jobId) => {
  await openSpace();
  await openGraph(app, String(jobId));
  const index = space.graph?.items.findIndex((item) => item.kind === "trajectory") ?? -1;
  if (index >= 0) await chooseGraph(index);
};

// ---- データ -------------------------------------------------------------------------------

async function loadData() {
  if (state.conversation === null) {
    space.data = {items: [], jobs: [], notes: []};
  } else {
    const [conversation, materials] = await Promise.all([api(`conversations/${state.conversation}`),
                                                          api(`conversations/${state.conversation}/materials`)]);
    space.data = {items: conversation.items, jobs: materials.jobs, notes: materials.notes};
  }
  if (space.mode === "notes" && !space.note) space.notes = await api("notes").catch(() => space.notes);
  redraw();
}

async function openGraph(app, jobId) {
  space.graph = {app, jobId, items: [], index: 0, yIndex: 0, table: null, image: null, error: null, menu: null, menuPage: 0};
  redraw();
  try {
    const query = state.conversation === null ? "" : `?conversation_id=${state.conversation}`;
    const view = await api(`views/${encodeURIComponent(app)}/jobs/${encodeURIComponent(jobId)}${query}`);
    // 表 (グラフに描く) と、図 (アプリが描いた png。作業のファイル) を 1 つの一覧に
    space.graph.workId = view.work_id;
    space.graph.final = view.final;   // 最終構造を構造として登録する道具 (あれば「最終構造を利用する」)
    space.graph.items = [
      ...view.datasets.map((dataset) => ({kind: "table", title: dataset.title, dataset})),
      ...(view.work_id ? (view.images || []).map((path) => ({kind: "image", title: `図: ${path.split("/").pop()}`, path})) : []),
      ...(view.trajectory ? [{kind: "trajectory", title: "構造の時間変化"}] : []),
    ];
    if (!space.graph.items.length) throw new Error("グラフにできる表や図はまだありません");
    await chooseGraph(0);
  } catch (error) {
    space.graph.error = error.message;
    redraw();
  }
}

async function chooseGraph(index) {
  const graph = space.graph;
  graph.index = index;
  graph.menu = null;
  graph.table = null;
  graph.xName = null;   // 表を変えたら横軸は表の既定に戻す
  graph.image = null;
  graph.imageError = null;
  redraw();
  const item = graph.items[index];
  if (item.kind === "trajectory") {
    await startMotion(graph.app, graph.jobId);
    return;
  }
  if (item.kind === "image") {
    const image = new Image();
    image.onload = () => redraw();
    image.onerror = () => { graph.imageError = "図を読み込めませんでした"; redraw(); };
    image.src = `/api/works/${graph.workId}/image?path=${encodeURIComponent(item.path)}`;
    graph.image = image;
    return;
  }
  try {
    graph.table = await api(`views/${encodeURIComponent(graph.app)}/jobs/${encodeURIComponent(graph.jobId)}/data?key=${encodeURIComponent(item.dataset.key)}`);
    const ys = yChoices(item.dataset, graph.table);
    const preferred = item.dataset.y.find((n) => n in graph.table.columns);
    graph.yIndex = ys.length && ys[0].lines.length > 1 ? 0 : Math.max(0, ys.findIndex((y) => y.lines[0] === preferred));
  } catch (error) {
    graph.error = error.message;
  }
  redraw();
}

// 最終構造 (構造緩和の後など) を構造として登録し、目の前に出す。会話にも 3D のカードが出る
async function useFinal() {
  const graph = space.graph;
  if (!graph?.final || graph.saving) return;
  graph.saving = "登録中…";
  redraw();
  try {
    const conversation = await benchConversation();
    const data = await api(`conversations/${conversation}/tools`, {method: "POST",
      body: JSON.stringify({name: graph.final.name, args: {[graph.final.id_arg]: Number(graph.jobId) || graph.jobId}})});
    if (!data.ok) throw new Error(prettyResult(data.result));
    graph.saving = null;
    await showStructure({app: graph.app, structure_id: String(JSON.parse(data.result).id)});
  } catch (error) {
    graph.saving = null;
    graph.error = error.message;
    redraw();
  }
}

// ---- 構造の時間変化 (計算過程の構造のコマを、目の前の構造の場所で再生する) --------------------------------

const MOTION_MS = 200;   // コマを送る間隔

async function startMotion(app, jobId) {
  stopMotion();
  const motion = {app, jobId, data: null, index: 0, playing: false, timer: null, error: null};
  space.motion = motion;
  redraw();
  try {
    motion.data = await api(`views/${encodeURIComponent(app)}/jobs/${encodeURIComponent(jobId)}/trajectory`);
    if (!motion.data.frames.length) throw new Error("このジョブには構造の時間変化 (トラジェクトリ) がありません");
  } catch (error) {
    motion.error = error.message;
    redraw();
    return;
  }
  if (space.motion !== motion) return;
  showFrame(0);
  playMotion();   // 開いたら再生する
}

// コマ i を目の前に出す。手やドラッグで回した向きは引きつぐ
function showFrame(i) {
  const motion = space.motion;
  if (!motion?.data || imd.active) return;
  const st = space.structure;
  const frame = motion.data.frames[i];
  motion.index = i;
  const rotation = st.group ? st.group.rotation.clone() : null;
  if (st.group) {
    space.scene.remove(st.group);
    disposeGroup(st.group);
  }
  st.data = {name: `時間変化: ${motion.app} ジョブ ${motion.jobId}`, formula: "", symbols: motion.data.symbols,
             positions: frame.positions, cell: frame.cell, n_atoms: motion.data.n_atoms, repeat: [1, 1, 1]};
  st.group = structureGroup(st.data, STRUCTURE_SIZE, {zUp: true});
  st.group.position.copy(STRUCTURE_AT);
  if (rotation) st.group.rotation.copy(rotation);
  st.spinning = false;   // コマを見比べやすいよう、勝手には回さない
  space.scene.add(st.group);
  refreshHits();
  redraw();
}

function playMotion() {
  const motion = space.motion;
  if (!motion?.data) return;
  if (motion.playing) {
    clearInterval(motion.timer);
    motion.playing = false;
  } else {
    motion.playing = true;
    motion.timer = setInterval(() => {
      if (!space.open || space.motion !== motion) return clearInterval(motion.timer);
      showFrame((motion.index + 1) % motion.data.frames.length);
    }, MOTION_MS);
  }
  redraw();
}

function stepMotion(step) {
  const motion = space.motion;
  if (!motion?.data) return;
  if (motion.playing) playMotion();   // 止めてから送る
  showFrame((motion.index + step + motion.data.frames.length) % motion.data.frames.length);
}

function stopMotion() {
  if (space.motion) clearInterval(space.motion.timer);
  space.motion = null;
}

function drawMotion(g, panel, box) {
  const motion = space.motion;
  g.font = "26px system-ui, sans-serif";
  if (!motion || motion.error || !motion.data) {
    g.fillStyle = motion?.error ? color("--bad") : color("--muted");
    for (const [i, line] of wrap(g, motion?.error || "読み込み中…", box.w).entries()) g.fillText(line, box.x, box.y + 40 + i * 36);
    return;
  }
  const n = motion.data.frames.length;
  const frame = motion.data.frames[motion.index];
  g.fillStyle = color("--fg");
  g.fillText(`コマ ${motion.index + 1} / ${n} (トラジェクトリの ${frame.index + 1} 番目)`, box.x, box.y + 40);
  g.fillStyle = color("--muted");
  g.font = "22px system-ui, sans-serif";
  g.fillText(`${motion.data.n_atoms} 原子・全 ${motion.data.n_frames_total} コマから ${n} コマ。構造は目の前に出ます (つかんで回せます)`.slice(0, 46),
             box.x, box.y + 78);
  // 進み具合のバー。押した所のコマへ飛ぶ
  const bar = {x: box.x, y: box.y + 110, w: box.w, h: 36};
  g.fillStyle = color("--accent-soft");
  g.fillRect(bar.x, bar.y, bar.w, bar.h);
  g.fillStyle = color("--accent");
  g.fillRect(bar.x, bar.y, bar.w * (motion.index + 1) / n, bar.h);
  panel.regions.push({...bar, action: (at) => stepMotion(Math.min(n - 1, Math.floor((at.x - bar.x) / bar.w * n)) - motion.index)});
  const third = (box.w - 32) / 3;
  const y = bar.y + bar.h + 30;
  button(g, panel, box.x, y, third, 72, "◀ コマ", () => stepMotion(-1));
  button(g, panel, box.x + third + 16, y, third, 72, motion.playing ? "止める" : "▶ 再生", () => playMotion(), motion.playing);
  button(g, panel, box.x + (third + 16) * 2, y, third, 72, "コマ ▶", () => stepMotion(1));
  g.font = "22px system-ui, sans-serif";
  g.fillStyle = color("--muted");
  g.fillText("元の構造に戻るときは、構造のパネルの「◀ 前」「次 ▶」", box.x, y + 120);
}

function redraw() {
  if (!space.scene) return;
  const [chat, jobs, graph, structure] = space.panels;
  drawChat(chat);
  drawJobs(jobs);
  drawGraph(graph);
  drawStructure(structure);
}

// ---- 場面 ---------------------------------------------------------------------------------

function build() {
  const canvas = document.getElementById("space-canvas");
  const renderer = new THREE.WebGLRenderer({canvas, antialias: true, alpha: true});
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.xr.enabled = true;
  renderer.xr.setReferenceSpaceType("local-floor");
  const scene = new THREE.Scene();
  // スマホ・PC: その場に立って見回す (ドラッグで向き、ホイールやピンチで寄る)。XR では頭の向きがそのまま使われる
  const camera = new THREE.PerspectiveCamera(65, 1, 0.05, 50);
  camera.position.set(0, 1.4, 0.35);
  camera.rotation.order = "YXZ";

  // 床の輪と格子 (HUD の雰囲気と、上下の手がかり)
  const ring = new THREE.Mesh(new THREE.RingGeometry(RADIUS - 0.02, RADIUS, 96),
                              new THREE.MeshBasicMaterial({color: color("--accent"), transparent: true, opacity: 0.45, side: THREE.DoubleSide}));
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.01;
  const grid = new THREE.PolarGridHelper(RADIUS, 12, 6, 96, color("--line-strong"), color("--line-strong"));
  grid.material.transparent = true;
  grid.material.opacity = 0.25;
  scene.add(ring, grid);

  space.panels = [
    new Panel({title: "会話", width: 1.1, height: 1.15, angle: 0}),
    new Panel({title: "資料", width: 0.85, height: 1.25, angle: -0.64, y: 1.42}),
    new Panel({title: "グラフ", width: 1.0, height: 0.85, angle: 0.68}),
    new Panel({title: "構造", width: 0.8, height: 0.6, angle: 0.68, y: 0.66}),
  ];
  for (const panel of space.panels) scene.add(panel.mesh);
  space.hits = space.panels.map((p) => p.mesh);

  // XR のコントローラー (手でつまむのも select になる)。光線を出す
  for (const index of [0, 1]) {
    const controller = renderer.xr.getController(index);
    const ray = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -4)]),
                               new THREE.LineBasicMaterial({color: color("--accent")}));
    controller.add(ray);
    // 構造の上でつまむ・握る: 動かした分だけ回す。ほとんど動かさなければ押したことにする
    controller.addEventListener("selectstart", () => {
      const hit = hitFrom(controller);
      const atom = imdAtom(hit);
      if (atom !== null) {   // 対話型 MD の原子をつまむ: 光線の先の同じ距離に原子を引っぱる
        space.xrDrag = {controller, index: atom, distance: hit.distance};
        return;
      }
      if (hit && !hit.object.userData.panel) {
        space.grab = {controller, group: groupOf(hit.object), last: controller.getWorldPosition(new THREE.Vector3()), moved: 0};
      }
    });
    controller.addEventListener("select", () => {
      if (space.xrDrag?.controller === controller) return;
      if (space.grab?.controller === controller) {
        if (space.grab.moved < 0.03) press(hitFrom(controller));
        space.grab = null;
      } else {
        pressFrom(controller);
      }
    });
    controller.addEventListener("selectend", () => {
      if (space.grab?.controller === controller) space.grab = null;
      if (space.xrDrag?.controller === controller) {
        space.xrDrag = null;
        imdSend({op: "release"});
      }
    });
    controller.addEventListener("squeezestart", () => {   // グリップで握る: 対話型 MD の構造ごと回す
      const hit = hitFrom(controller);
      if (imd.group && hit && groupOf(hit.object) === imd.group) {
        space.grab = {controller, group: imd.group, last: controller.getWorldPosition(new THREE.Vector3()), moved: 0, squeeze: true};
      }
    });
    controller.addEventListener("squeezeend", () => { if (space.grab?.controller === controller) space.grab = null; });
    scene.add(controller);
  }

  Object.assign(space, {renderer, scene, camera});
  canvas.addEventListener("pointerdown", (event) => {
    canvas.setPointerCapture(event.pointerId);
    space.pointers.set(event.pointerId, [event.clientX, event.clientY]);
    space.lastPointer = [event.clientX, event.clientY];
    const hit = space.pointers.size === 1 ? hitAt(event) : null;
    const atom = imdAtom(hit);
    if (atom !== null) {   // 対話型 MD の原子: 画面と平行な面の上で動かす
      space.dragAtom = {index: atom, plane: new THREE.Plane().setFromNormalAndCoplanarPoint(
        space.camera.getWorldDirection(new THREE.Vector3()), hit.point)};
      space.turning = null;
      return;
    }
    space.turning = hit && !hit.object.userData.panel ? groupOf(hit.object) : null;   // 構造の上からのドラッグはその構造を回す
  });
  canvas.addEventListener("pointermove", (event) => {
    const before = space.pointers.get(event.pointerId);
    if (!before) return;
    if (space.pointers.size === 2) {   // 2 本の指: 間隔で寄る・離れる
      const [a, b] = [...space.pointers.values()];
      const start = Math.hypot(a[0] - b[0], a[1] - b[1]);
      space.pointers.set(event.pointerId, [event.clientX, event.clientY]);
      const [c, d] = [...space.pointers.values()];
      zoom(start / Math.max(1, Math.hypot(c[0] - d[0], c[1] - d[1])));
      return;
    }
    space.pointers.set(event.pointerId, [event.clientX, event.clientY]);
    if (space.dragAtom) {
      const rect = canvas.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1,
                                                -((event.clientY - rect.top) / rect.height) * 2 + 1), space.camera);
      const point = raycaster.ray.intersectPlane(space.dragAtom.plane, new THREE.Vector3());
      if (point) imdPull(space.dragAtom.index, point);
      return;
    }
    const [dx, dy] = [event.clientX - before[0], event.clientY - before[1]];
    const group = space.turning;
    if (group) {
      group.rotation.y += dx * 0.01;
      group.rotation.x = Math.max(-1.5, Math.min(1.5, group.rotation.x + dy * 0.01));
      if (group === space.structure.group) space.structure.spinning = false;
      return;
    }
    look(dx, dy, (space.camera.fov / 65) * 0.0025);
  });
  const release = (event) => {
    space.pointers.delete(event.pointerId);
    if (space.dragAtom) {
      space.dragAtom = null;
      imdSend({op: "release"});
      return;
    }
    if (!space.pointers.size) space.turning = null;
    const [x, y] = space.lastPointer || [0, 0];
    if (event.type === "pointerup" && Math.hypot(event.clientX - x, event.clientY - y) < 8) pressAt(event);   // ドラッグは押したことにしない
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    zoom(Math.exp(event.deltaY * 0.001));
  }, {passive: false});
  addEventListener("resize", resize);
  resize();
  look(0, 0, 0);
  renderer.setAnimationLoop(() => {
    if (space.xrDrag) {   // VR でつまんでいる原子を、光線の先へ引っぱる
      const {controller, index, distance} = space.xrDrag;
      const matrix = new THREE.Matrix4().extractRotation(controller.matrixWorld);
      const origin = new THREE.Vector3().setFromMatrixPosition(controller.matrixWorld);
      imdPull(index, origin.add(new THREE.Vector3(0, 0, -1).applyMatrix4(matrix).multiplyScalar(distance)));
    }
    const st = space.structure;
    const grab = space.grab;
    if (grab) {   // XR で掴んでいる: 手の動きで、掴んだ構造を回す
      const now = grab.controller.getWorldPosition(new THREE.Vector3());
      const delta = now.clone().sub(grab.last);
      grab.group.rotation.y += delta.x * 6;
      grab.group.rotation.x = Math.max(-1.5, Math.min(1.5, grab.group.rotation.x - delta.y * 6));
      grab.moved += delta.length();
      grab.last = now;
      if (grab.moved >= 0.03 && grab.group === st.group) st.spinning = false;
    }
    if (st.group && st.spinning && grab?.group !== st.group) {
      st.group.rotation.y += 0.004;
    }
    renderer.render(scene, camera);
  });
}

const raycaster = new THREE.Raycaster();

// 当たった原子や線から、掴む単位 (構造ひとつ) をたどる
function groupOf(object) {
  while (object && !space.hits.includes(object)) object = object.parent;
  return object;
}

function hitAt(event) {
  const rect = space.renderer.domElement.getBoundingClientRect();
  const pointer = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1);
  raycaster.setFromCamera(pointer, space.camera);
  return raycaster.intersectObjects(space.hits, true)[0];
}

function hitFrom(controller) {
  const matrix = new THREE.Matrix4().extractRotation(controller.matrixWorld);
  raycaster.ray.origin.setFromMatrixPosition(controller.matrixWorld);
  raycaster.ray.direction.set(0, 0, -1).applyMatrix4(matrix);
  return raycaster.intersectObjects(space.hits, true)[0];
}

function pressAt(event) {
  press(hitAt(event));
}

function pressFrom(controller) {
  press(hitFrom(controller));
}

// パネルは押した場所のボタン、構造は押すと回転を止める・回す
function press(hit) {
  if (!hit) return;
  const panel = hit.object.userData.panel;
  if (panel && hit.uv) {
    panel.press(hit.uv);
  } else if (groupOf(hit.object) === space.structure.group) {
    space.structure.spinning = !space.structure.spinning;
    redraw();
  }
}

function resize() {
  if (!space.renderer || space.renderer.xr.isPresenting) return;
  const canvas = space.renderer.domElement;
  const {clientWidth: w, clientHeight: h} = canvas.parentElement;
  space.renderer.setSize(w, h, false);
  space.camera.aspect = w / h;
  // 縦長の画面では少し下がって広く見る
  space.camera.position.set(0, 1.4, w < h ? 0.9 : 0.35);
  space.camera.updateProjectionMatrix();
}

// 見回す: 横は左右に 80° まで、縦は上下に 35° まで
function look(dx, dy, speed) {
  const view = space.look;
  view.yaw = Math.max(-1.4, Math.min(1.4, view.yaw + dx * speed));
  view.pitch = Math.max(-0.6, Math.min(0.6, view.pitch + dy * speed));
  space.camera.rotation.set(view.pitch, view.yaw, 0);
}

function zoom(factor) {
  space.camera.fov = Math.max(30, Math.min(85, space.camera.fov * factor));
  space.camera.updateProjectionMatrix();
}

// ---- 開く・閉じる・XR -------------------------------------------------------------------------

async function openSpace() {
  document.getElementById("space").hidden = false;
  space.open = true;
  if (!space.scene) build();
  resize();
  await loadData().catch(() => {});
  await followStructures().catch(() => {});
  clearInterval(space.follow);
  space.follow = setInterval(() => followStructures().catch(() => {}), FOLLOW_SECONDS * 1000);
  const xrButton = document.getElementById("enter-xr");
  const mode = await xrMode();
  xrButton.hidden = !mode;
  xrButton.dataset.mode = mode || "";
}

function closeSpace() {
  space.open = false;
  stopImd();
  stopMotion();
  clearInterval(space.follow);
  space.xrSession?.end();
  document.getElementById("space").hidden = true;
}

async function xrMode() {
  if (!navigator.xr) return null;
  for (const mode of ["immersive-ar", "immersive-vr"]) {
    if (await navigator.xr.isSessionSupported(mode).catch(() => false)) return mode;
  }
  return null;
}

async function enterXR() {
  const mode = document.getElementById("enter-xr").dataset.mode;
  if (!mode) return;
  const session = await navigator.xr.requestSession(mode, {optionalFeatures: ["local-floor", "hand-tracking"]});
  space.xrSession = session;   // 背景は描かないので、immersive-ar ではパススルーで部屋が見える
  await space.renderer.xr.setSession(session);
  session.addEventListener("end", () => {
    space.xrSession = null;
    resize();
  });
}

// 声の状態が変わったら「話す」の板を描き直す (voice.js から呼ぶ)
window.spaceRedraw = () => {
  if (space.open) redraw();
};

// 会話に動きがあったら描き直す (app.js から呼ぶ)
window.spaceRefresh = () => {
  if (space.open) loadData().catch(() => {});
};

document.getElementById("open-space").addEventListener("click", () => openSpace());
document.getElementById("close-space").addEventListener("click", () => closeSpace());
document.getElementById("enter-xr").addEventListener("click", () => enterXR().catch((error) => alert(`XR に入れませんでした: ${error.message}`)));
document.getElementById("space-mic").addEventListener("click", () => toggleListening());
