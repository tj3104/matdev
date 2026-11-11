"use strict";

// ---- ジョブのグラフ (ノートと資料で使う) -----------------------------------------------------
// 結果はつないだアプリの道具からサーバーが直接読む (LLM は使わない)。

async function openJob(viewer, jobs, button, appName, jobId) {
  const wasOpen = button.getAttribute("aria-pressed") === "true";
  for (const other of jobs.children) other.setAttribute("aria-pressed", "false");
  viewer.replaceChildren();
  if (wasOpen) return;
  button.setAttribute("aria-pressed", "true");
  await showJob(viewer, appName, jobId);
}

// ジョブの結果を viewer の中に出す: アプリの画面へのリンク・グラフ (表と軸を選ぶ)・図・構造の時間変化。
// 資料のジョブのボタンと、会話の「計算が終わりました」のカードで使う
async function showJob(viewer, appName, jobId) {
  const base = `views/${encodeURIComponent(appName)}/jobs/${encodeURIComponent(jobId)}`;
  const query = state.conversation === null ? "" : `?conversation_id=${state.conversation}`;
  viewer.append(Object.assign(document.createElement("p"), {className: "notice", textContent: "読み込み中…"}));
  let view;
  try {
    view = await api(base + query);
  } catch (error) {
    viewer.replaceChildren(Object.assign(document.createElement("p"), {className: "notice error", textContent: error.message}));
    return;
  }
  viewer.replaceChildren();
  if (view.link) {
    viewer.append(Object.assign(document.createElement("a"), {href: view.link, target: "_blank", rel: "noopener noreferrer",
      className: "open-app", textContent: `${appName} でジョブ ${jobId} を開く`}));
  }
  if (view.final) viewer.append(finalSection(view.final, jobId));
  const extras = jobFiles(view);   // 図 (png) と Plotly の図 (html)。作業のディレクトリの中のものだけ
  if (view.trajectory) extras.push(trajectorySection(appName, jobId));
  if (!view.datasets.length) {
    if (!extras.length && state.apps[appName]?.graphs) {
      viewer.append(Object.assign(document.createElement("p"), {className: "notice", textContent: "グラフにできる表はまだありません"}));
    }
    viewer.append(...extras);
    return;
  }
  const controls = Object.assign(document.createElement("div"), {className: "chart-controls"});
  const tableSelect = labeled(controls, "表");
  const xSelect = labeled(controls, "横軸");
  const ySelect = labeled(controls, "縦軸");
  const figure = Object.assign(document.createElement("div"), {className: "chart"});
  viewer.append(controls, figure);
  for (const dataset of view.datasets) tableSelect.append(new Option(dataset.title, dataset.key));
  let table = null;

  const draw = () => {
    const dataset = view.datasets.find((d) => d.key === tableSelect.value);
    drawChart(figure, table, dataset, ySelect.value, xSelect.value);
  };
  // 縦軸の選択肢は、横軸に選んだ列を除いたもの。線が複数ある表 (バンド・部分 DOS) は「すべて」を先頭に
  const fillY = (dataset, keep) => {
    const x = xSelect.value;
    const labels = Object.fromEntries(dataset.columns.map((c) => [c.key, c.label]));
    ySelect.replaceChildren();
    const several = dataset.y.filter((name) => name in table.columns && name !== x);
    if (several.length > 1) ySelect.append(new Option(`すべて (${several.length} 本)`, ALL));
    for (const name of Object.keys(table.columns)) {
      if (name !== x) ySelect.append(new Option(labels[name] || name, name));
    }
    if (keep && [...ySelect.options].some((o) => o.value === keep)) ySelect.value = keep;
    else if (several.length > 1) ySelect.value = ALL;
    else if (several.length) ySelect.value = several[0];
  };
  const load = async () => {
    const dataset = view.datasets.find((d) => d.key === tableSelect.value);
    figure.replaceChildren(Object.assign(document.createElement("p"), {className: "notice", textContent: "読み込み中…"}));
    try {
      table = await api(`${base}/data?key=${encodeURIComponent(dataset.key)}`);
    } catch (error) {
      figure.replaceChildren(Object.assign(document.createElement("p"), {className: "notice error", textContent: error.message}));
      return;
    }
    const labels = Object.fromEntries(dataset.columns.map((c) => [c.key, c.label]));
    xSelect.replaceChildren(...Object.keys(table.columns).map((name) => new Option(labels[name] || name, name)));
    xSelect.value = xColumn(dataset, table);
    fillY(dataset);
    draw();
  };
  tableSelect.addEventListener("change", load);
  xSelect.addEventListener("change", () => {
    fillY(view.datasets.find((d) => d.key === tableSelect.value), ySelect.value);
    draw();
  });
  ySelect.addEventListener("change", draw);
  viewer.append(...extras);
  await load();
}

// 最終構造 (構造緩和の後など) を構造として登録する。会話に 3D のカードが出て、そこから編集・計算・その場で MD に進める
function finalSection(final, jobId) {
  const box = Object.assign(document.createElement("div"), {className: "structure-tools final-structure"});
  const button = Object.assign(document.createElement("button"), {type: "button", className: "small", textContent: "最終構造を利用する"});
  const note = Object.assign(document.createElement("small"), {className: "meta", textContent: "計算の後の構造を、次の編集・計算に使う"});
  button.addEventListener("click", async () => {
    button.disabled = true;
    note.textContent = "登録しています…";
    try {
      const conversation = await benchConversation();
      const data = await api(`conversations/${conversation}/tools`,
                             {method: "POST", body: JSON.stringify({name: final.name, args: {[final.id_arg]: Number(jobId) || jobId}})});
      if (!data.ok) throw new Error(prettyResult(data.result));
      const made = JSON.parse(data.result).id;
      note.textContent = `構造 ${made} として登録しました。会話の 3D のカードから編集・計算に進めます`;
    } catch (error) {
      note.textContent = error.message;
      note.className = "notice error";
      button.disabled = false;
    }
  });
  box.append(button, note);
  return box;
}

// 構造の時間変化: 押すと計算過程の構造を読み、3D でコマ送り・再生する (structure3d.js の mountTrajectory)
function trajectorySection(appName, jobId) {
  const box = Object.assign(document.createElement("div"), {className: "trajectory"});
  const button = Object.assign(document.createElement("button"), {type: "button", className: "small", textContent: "構造の時間変化を見る"});
  button.addEventListener("click", async () => {
    button.disabled = true;
    button.textContent = "読み込み中…";
    try {
      const data = await api(`views/${encodeURIComponent(appName)}/jobs/${encodeURIComponent(jobId)}/trajectory`);
      if (!data.frames.length) {
        box.replaceChildren(Object.assign(document.createElement("p"), {className: "notice", textContent: "このジョブには構造の時間変化 (トラジェクトリ) がありません"}));
        return;
      }
      box.replaceChildren();
      window.mountTrajectory(box, data);
      box.querySelector(".structure-tools")?.append(toSpace);   // 再生の横にも「空間で見る」
    } catch (error) {
      button.disabled = false;
      button.textContent = "構造の時間変化を見る";
      box.append(Object.assign(document.createElement("p"), {className: "notice error", textContent: error.message}));
    }
  });
  const toSpace = Object.assign(document.createElement("button"), {type: "button", className: "quiet small", textContent: "空間で見る"});
  toSpace.addEventListener("click", () => window.openSpaceMotion?.(appName, jobId));
  box.append(h("div", {className: "structure-tools"}, button, toSpace));
  return box;
}

// ジョブの図: png は画像で、Plotly の html は封じた枠 (sandbox) で開く。押すと大きくなる
function jobFiles(view) {
  if (!view.work_id || !(view.images?.length || view.html?.length)) return [];
  const box = Object.assign(document.createElement("div"), {className: "job-files"});
  for (const path of view.html || []) {
    box.append(plotlyFrame(view.work_id, path));
  }
  for (const path of view.images || []) {
    const image = Object.assign(document.createElement("img"), {className: "file-image", alt: path, loading: "lazy",
      src: `/api/works/${view.work_id}/image?path=${encodeURIComponent(path)}`});
    const caption = Object.assign(document.createElement("small"), {className: "meta", textContent: path});
    box.append(caption, image);
  }
  return [box];
}

function plotlyFrame(workId, path) {
  const holder = Object.assign(document.createElement("div"), {className: "plotly"});
  const head = Object.assign(document.createElement("small"), {className: "meta", textContent: `${path} (回せる図。Plotly)`});
  const frame = document.createElement("iframe");
  frame.setAttribute("sandbox", "allow-scripts");   // 別の場所として動かす (MatDev のログインや API には触れない)
  frame.loading = "lazy";
  frame.title = path;
  frame.src = `/api/works/${workId}/html?path=${encodeURIComponent(path)}`;
  holder.append(head, frame);
  return holder;
}

function labeled(parent, text) {
  const label = Object.assign(document.createElement("label"), {textContent: text});
  const select = document.createElement("select");
  label.append(select);
  parent.append(label);
  return select;
}

function xColumn(dataset, table) {
  if (dataset.x && dataset.x in table.columns) return dataset.x;
  return Object.keys(table.columns)[0];
}

const SVG = "http://www.w3.org/2000/svg";

function svg(name, attributes = {}, text) {
  const node = document.createElementNS(SVG, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function ticks(low, high, count = 5) {
  if (low === high) return [low];
  const raw = (high - low) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= raw);
  const values = [];
  for (let v = Math.ceil(low / step) * step; v <= high + step * 1e-9; v += step) values.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return values;
}

function format(value) {
  const size = Math.abs(value);
  if (size !== 0 && (size >= 1e5 || size < 1e-3)) return value.toExponential(1);
  return String(Number(value.toPrecision(4)));
}

// 折れ線。yName が "__all__" なら dataset.y の列をすべて重ねる (バンド・部分 DOS など)。x と y の値が両方ある点だけを使う
const ALL = "__all__";
const SERIES_COLORS = ["var(--accent)", "#f2a541", "#9b8cff", "#5fd38d", "#ff7aa8", "#c8d85c"];

// xName を省くと、表の既定の横軸
function drawChart(figure, table, dataset, yName, xName = null) {
  figure.replaceChildren();
  if (!table || !yName) {
    figure.append(Object.assign(document.createElement("p"), {className: "notice", textContent: "縦軸にできる列がありません"}));
    return;
  }
  xName = xName && xName in table.columns ? xName : xColumn(dataset, table);
  const labels = Object.fromEntries(dataset.columns.map((c) => [c.key, c.label]));
  const names = yName === ALL ? dataset.y.filter((n) => n in table.columns && n !== xName) : [yName];
  const xs = table.columns[xName];
  const series = names.map((name) => {
    const ys = table.columns[name] || [];
    const points = [];
    for (let i = 0; i < Math.min(xs.length, ys.length); i++) {
      if (xs[i] !== null && ys[i] !== null) points.push([xs[i], ys[i]]);
    }
    return {name, points};
  }).filter((s) => s.points.length);
  const all = series.flatMap((s) => s.points);
  if (!all.length) {
    figure.append(Object.assign(document.createElement("p"), {className: "notice", textContent: "表示できる値がありません"}));
    return;
  }
  // 描く幅を画面の幅に合わせる (縮めて描くとスマホで文字が小さくなる)
  const W = Math.round(Math.min(720, Math.max(300, figure.clientWidth - 16)));
  const H = Math.round(Math.min(380, Math.max(220, W * 0.62))), L = 58, R = 12, T = 12, B = 46;
  let [x0, x1] = [Math.min(...all.map((p) => p[0])), Math.max(...all.map((p) => p[0]))];
  let [y0, y1] = [Math.min(...all.map((p) => p[1])), Math.max(...all.map((p) => p[1]))];
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  if (y0 === y1) { y0 -= Math.abs(y0) * 0.05 || 1; y1 += Math.abs(y1) * 0.05 || 1; }
  const pad = (y1 - y0) * 0.05;
  y0 -= pad; y1 += pad;
  const sx = (v) => L + (v - x0) / (x1 - x0) * (W - L - R);
  const sy = (v) => H - B - (v - y0) / (y1 - y0) * (H - T - B);
  const yLabel = yName === ALL ? `${names.length} 本` : labels[yName] || yName, xLabel = labels[xName] || xName;
  const chart = svg("svg", {viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${dataset.title}: ${xLabel} と ${yLabel}`});
  for (const v of ticks(y0, y1)) {
    chart.append(svg("line", {x1: L, x2: W - R, y1: sy(v), y2: sy(v), class: "grid"}),
                 svg("text", {x: L - 6, y: sy(v) + 4, "text-anchor": "end"}, format(v)));
  }
  for (const v of ticks(x0, x1, W < 480 ? 4 : 6)) {
    chart.append(svg("line", {x1: sx(v), x2: sx(v), y1: T, y2: H - B, class: "grid"}),
                 svg("text", {x: sx(v), y: H - B + 18, "text-anchor": "middle"}, format(v)));
  }
  chart.append(svg("rect", {x: L, y: T, width: W - L - R, height: H - T - B, class: "frame"}));
  // 線が多いとき (バンドなど) は同じ色で細く、少ないときは色分けして凡例を付ける
  const many = series.length > SERIES_COLORS.length;
  series.forEach((s, i) => {
    const path = s.points.map((p, j) => `${j ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");
    const line = svg("path", {d: path, class: many ? "series thin" : "series"});
    if (!many) line.style.stroke = SERIES_COLORS[i];   // style 属性は CSP で効かないので、スクリプトから付ける
    chart.append(line);
  });
  if (series.length === 1 && series[0].points.length <= 40) {
    for (const p of series[0].points) chart.append(svg("circle", {cx: sx(p[0]), cy: sy(p[1]), r: 3, class: "dot"}));
  }
  chart.append(svg("text", {x: (L + W - R) / 2, y: H - 8, "text-anchor": "middle", class: "axis-label"}, xLabel));
  figure.append(Object.assign(document.createElement("p"), {className: "chart-title", textContent: `${dataset.title} / ${yLabel}`}), chart);
  if (series.length > 1 && !many) {
    const legend = Object.assign(document.createElement("div"), {className: "legend"});
    series.forEach((s, i) => {
      const chip = document.createElement("span");
      const dot = document.createElement("i");
      dot.style.background = SERIES_COLORS[i];
      chip.append(dot, labels[s.name] || s.name);
      legend.append(chip);
    });
    figure.append(legend);
  }
}
