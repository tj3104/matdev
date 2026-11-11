"use strict";

// ---- 作業と作業のファイル (普段の画面) --------------------------------------------------------------
// 会話ごとに出力先の作業を持つ。会話を始める前・出力先のない会話を開いたときに、選ぶか作ってもらう。
// 資料のパネルの「作業」でファイルをたどり、押すとウインドウで開く (いくつでも並べられる)。
// 開いているウインドウは、次の依頼で [画面で開いているもの] としてジャービスに伝える (空間のウインドウも合わせて)。

const works = {active: null, roots: [], directory: "", windows: []};

function showWork(work, roots) {
  works.active = work;
  if (roots) works.roots = roots;
  for (const id of ["work-name", "work-chip"]) {
    const node = document.getElementById(id);
    node.textContent = work ? work.name : "出力先なし";
    node.title = work ? `出力先: ${work.path}` : "この会話の計算の結果を置くディレクトリを決めてください";
    node.classList.toggle("missing", !work);
  }
}

// 出力先を選ぶか作る。決めた作業を返す (やめたら null)。required なら「やめる」を出さない説明を付ける
function pickWork({title = "出力先 (作業) を決める", note = ""} = {}) {
  return chooseWork({title, note, pickOnly: true});
}

async function chooseWork({title = "作業", note = "", pickOnly = false} = {}) {
  const {works: list, active} = await api("works");
  const dialog = Object.assign(document.createElement("dialog"), {className: "hud dialog"});
  const form = document.createElement("form");
  form.method = "dialog";
  const items = list.map((work) => {
    const label = Object.assign(document.createElement("label"), {className: "work-option"});
    const radio = Object.assign(document.createElement("input"), {type: "radio", name: "work", value: work.id, checked: active?.id === work.id});
    const text = document.createElement("span");
    text.append(Object.assign(document.createElement("b"), {textContent: work.name}),
                Object.assign(document.createElement("small"), {className: "meta", textContent: work.path}));
    label.append(radio, text);
    return label;
  });
  const name = Object.assign(document.createElement("input"), {maxLength: 60, placeholder: "新しい作業の名前 (例: Ti の相転移)"});
  const path = Object.assign(document.createElement("input"), {maxLength: 500, placeholder: `ディレクトリ (空なら ${works.roots[0] || ""}/名前)`});
  const error = Object.assign(document.createElement("p"), {className: "error"});
  const buttons = Object.assign(document.createElement("div"), {className: "dialog-buttons"});
  const cancel = Object.assign(document.createElement("button"), {value: "cancel", className: "quiet", textContent: "やめる"});
  const ok = Object.assign(document.createElement("button"), {value: "ok", className: "primary", textContent: "決める"});
  let decided = null;
  ok.addEventListener("click", async (event) => {
    event.preventDefault();
    try {
      if (name.value.trim()) {
        decided = await api("works", {method: "POST", body: JSON.stringify({name: name.value, path: path.value})});
      } else {
        const picked = form.querySelector("input[name=work]:checked");
        if (!picked) throw new Error("作業を選ぶか、新しく作ってください");
        decided = list.find((work) => work.id === Number(picked.value));
      }
      if (!pickOnly) {
        if (state.conversation !== null) {   // 開いている会話の出力先を変える
          await api(`conversations/${state.conversation}/work`, {method: "PUT", body: JSON.stringify({work_id: decided.id})});
        } else {
          await api("works/active", {method: "PUT", body: JSON.stringify({work_id: decided.id})});
        }
        showWork(decided);
        works.directory = "";
        if (panel.tab === "work") refreshPanel().catch(() => {});
      }
      dialog.close();
    } catch (problem) {
      error.textContent = problem.message;
    }
  });
  buttons.append(cancel, ok);
  form.append(Object.assign(document.createElement("p"), {className: "caption", textContent: title}),
              ...(note ? [Object.assign(document.createElement("p"), {className: "notice", textContent: note})] : []),
              ...(items.length ? items : [Object.assign(document.createElement("p"), {className: "notice", textContent: "作業はまだありません"})]),
              Object.assign(document.createElement("p"), {className: "caption", textContent: "新しく作る"}), name, path,
              Object.assign(document.createElement("small"), {className: "meta", textContent: `作れる場所: ${works.roots.join(", ")}`}),
              error, buttons);
  dialog.append(form);
  document.body.append(dialog);
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener("close", () => {
    dialog.remove();
    resolve(decided);
  }));
}

// 資料のパネルの「作業」: ディレクトリをたどる
async function workSection() {
  if (!works.active) {
    const box = section("作業");
    const button = el("button", "small", "作業を選ぶ・作る");
    button.type = "button";
    button.addEventListener("click", () => chooseWork());
    box.append(empty("作業を決めると、計算の結果と取ってきた構造がそのディレクトリに置かれ、ここで開けます"), button);
    return [box];
  }
  const data = await api(`works/${works.active.id}/files?directory=${encodeURIComponent(works.directory)}`);
  const box = section(`作業: ${works.active.name}`);
  const crumbs = el("div", "crumbs");
  const parts = data.directory ? data.directory.split("/") : [];
  const go = (directory) => { works.directory = directory; refreshPanel().catch(() => {}); };
  const top = el("button", "link", "一番上");
  top.type = "button";
  top.addEventListener("click", () => go(""));
  crumbs.append(top);
  parts.forEach((part, i) => {
    const link = el("button", "link", part);
    link.type = "button";
    link.addEventListener("click", () => go(parts.slice(0, i + 1).join("/")));
    crumbs.append(" / ", link);
  });
  box.append(crumbs);
  if (!data.entries.length) box.append(empty("まだ何もありません"));
  const icons = {dir: "📁", structure: "⚛", table: "📈", image: "🖼", html: "📊", text: "📄", other: "・"};
  for (const entry of data.entries) {
    const row = el("button", "file-row");
    row.type = "button";
    row.append(el("span", "", `${icons[entry.kind] || "・"} ${entry.name}`), el("small", "", entry.dir ? "" : sizeText(entry.size)));
    row.addEventListener("click", () => (entry.dir ? go(entry.path) : openWindow(entry.path)));
    box.append(row);
  }
  return [windowSection(), box];
}

function sizeText(size) {
  if (size === null || size === undefined) return "";
  return size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;
}

// ---- ウインドウ ----------------------------------------------------------------------------------

function windowSection() {
  const box = section("開いているウインドウ", works.windows.length);
  box.id = "work-windows";
  if (!works.windows.length) box.append(empty("ファイルを押すとここに開きます。いくつでも並べて見比べられます"));
  for (const win of works.windows) box.append(win.node);
  return box;
}

async function openWindow(path) {
  if (works.windows.some((w) => w.path === path)) return;
  const node = el("article", "card file-window");
  const head = el("div", "card-head");
  const close = el("button", "quiet icon", "✕");
  close.type = "button";
  close.setAttribute("aria-label", "閉じる");
  const toSpace = el("button", "quiet small", "空間に出す");
  toSpace.type = "button";
  toSpace.addEventListener("click", () => window.spaceOpenFile?.(path));
  head.append(el("b", "", path), el("span", "", ""), toSpace, close);
  const body = el("div", "file-body", "読み込み中…");
  node.append(head, body);
  const win = {path, node, describe: () => path};
  close.addEventListener("click", () => {
    works.windows = works.windows.filter((w) => w !== win);
    node.remove();
  });
  works.windows.unshift(win);
  refreshPanel().catch(() => {});
  try {
    const file = await api(`works/${works.active.id}/file?path=${encodeURIComponent(path)}`);
    fillWindow(win, body, file);
  } catch (error) {
    body.replaceChildren(el("p", "notice error", error.message));
  }
}

function fillWindow(win, body, file) {
  body.replaceChildren();
  if (file.kind === "structure") {
    const st = file.structure;
    const holder = el("div");
    body.append(holder);
    const legendNode = window.mountStructureData?.(holder, st);
    body.append(el("small", "meta", `${st.formula} / ${st.n_atoms} 原子`));
    if (legendNode) body.append(legendNode);
    body.append(...fileCalcButtons(file.path, {name: file.name, formula: st.formula}));
    win.describe = () => `構造 ${file.path} (${st.formula}, ${st.n_atoms} 原子)`;
  } else if (file.kind === "table") {
    const names = Object.keys(file.table.columns);
    const xSelect = labeled(body, "横軸");
    const ySelect = labeled(body, "縦軸");
    for (const name of names) {
      xSelect.append(new Option(name, name));
      ySelect.append(new Option(name, name));
    }
    ySelect.value = names[1];
    const figure = el("div", "chart");
    body.append(figure);
    const dataset = () => ({title: file.name, x: xSelect.value, y: [ySelect.value], columns: names.map((n) => ({key: n, label: n}))});
    const draw = () => drawChart(figure, file.table, dataset(), ySelect.value);
    xSelect.addEventListener("change", draw);
    ySelect.addEventListener("change", draw);
    draw();
    win.describe = () => `グラフ ${file.path} (横軸 ${xSelect.value}・縦軸 ${ySelect.value}、${file.table.n_rows} 行)`;
  } else if (file.kind === "image") {
    const image = el("img", "file-image");
    image.src = `/api/works/${works.active.id}/image?path=${encodeURIComponent(file.path)}`;
    image.alt = file.name;
    body.append(image);
    win.describe = () => `画像 ${file.path}`;
  } else if (file.kind === "html") {
    body.append(plotlyFrame(works.active.id, file.path));
    win.describe = () => `図 (Plotly) ${file.path}`;
  } else if (file.kind === "text") {
    body.append(el("pre", "file-text", file.text + (file.truncated ? "\n…(先頭だけ)" : "")));
    win.describe = () => `文字 ${file.path}`;
  } else {
    body.append(el("p", "notice", "この種類のファイルは画面では開けません"));
  }
}

// 構造ファイルの「計算」: 構造ファイルを受けるアプリ (ezcal など) から作業台で選ぶ
function fileCalcButtons(path, info) {
  const row = el("div", "structure-tools");
  const apps = calcAppsFor({source: "file", structure: path});
  if (apps.length) {
    const button = el("button", "small", "計算");
    button.type = "button";
    button.addEventListener("click", () => openWorkbench(apps[0], path, info, "calc"));
    row.append(button);
  }
  const live = el("button", "small", "その場で MD");   // 空間で MD を回しながら原子を動かす
  live.type = "button";
  live.addEventListener("click", () => window.openImd?.({file: path, label: info.name}));
  row.append(live);
  return [row];
}

// アプリの構造を、構造ファイルから計算するアプリに渡す: 作業の structures/ の cif を使う (なければ書き出す)
async function structureFileFor(app, structureId) {
  if (!works.active) throw new Error("この会話の出力先 (作業) を決めてください");
  const find = async () => {
    const listing = await api(`works/${works.active.id}/files?directory=structures`).catch(() => ({entries: []}));
    return listing.entries.find((e) => e.name.startsWith(`structure${structureId}_`) && e.name.endsWith(".cif"))?.path;
  };
  let path = await find();
  if (!path) {
    await api("works/save", {method: "POST", body: JSON.stringify({app, kind: "structure", id: String(structureId),
                                                                   conversation_id: state.conversation})});
    path = await find();
  }
  if (!path) throw new Error("構造を作業に書き出せませんでした");
  return path;
}

// 次の依頼に添える: 普段の画面と空間で開いているウインドウ
function viewingNow() {
  return [...works.windows.map((w) => w.describe()), ...(window.spaceViewing?.() || [])].slice(0, 12);
}

// app.js より先に読むので、ここでは $ を使わない
document.getElementById("work-choose").addEventListener("click", () => chooseWork({title: "この会話の出力先 (作業)"}).catch((error) => alert(error.message)));
document.getElementById("work-chip").addEventListener("click", () => chooseWork({title: "この会話の出力先 (作業)"}).catch((error) => alert(error.message)));
