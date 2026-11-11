"use strict";

// ---- 作業台: 表示した構造から編集と計算へ ---------------------------------------------------------
// 入力欄は道具が返す定義 (fields) から作る。プレビューと見積もりは確かめるだけ、保存と投入はサーバーの関門を通して会話に残す。
// 保存した構造は会話に 3D のカードで出て、そこからまた編集・計算に進める。
// 構造はアプリの構造の番号 (MatSim) か、作業の構造ファイルのパス (ezcal) で渡す。引数の名前はアプリの設定 (workbench) に従う。

const bench = {app: null, structure: null, info: null, ops: null, kinds: null, potentials: null, structures: null,
               form: null, mode: "edit", wb: {}};

function benchCall(action, args = {}, extra = {}) {
  return api(`workbench/${encodeURIComponent(bench.app)}`,
             {method: "POST", body: JSON.stringify({action, args, conversation_id: state.conversation, ...extra})});
}

function h(tag, props = {}, ...children) {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter((c) => c !== null && c !== undefined));
  return node;
}

// 表示した構造から開く。symbols は構造にある元素 (置換などの選択肢に使う)。
// 構造のもと (origin) はアプリの構造の番号か作業の構造ファイル。計算は、つないだアプリのうち、その構造を受け取れるものから選べる
// (構造ファイルを受けるアプリ (ezcal など) には、アプリの構造を作業の cif にして渡す)
async function openWorkbench(app, structure, info = {}, mode = "edit") {
  const wb = state.apps[app]?.workbench || {};
  bench.origin = {app, structure: wb.source === "file" ? String(structure) : Number(structure), source: wb.source || "structure"};
  bench.info = info;
  const calcApps = calcAppsFor(bench.origin);
  bench.mode = wb.edit && mode === "edit" ? "edit" : "calc";
  const dialog = $("workbench");
  const file = bench.origin.source === "file";
  $("bench-title").textContent = `${info.name || (file ? String(structure).split("/").pop() : `構造 ${structure}`)} の作業台`;
  $("bench-meta").textContent = file ? `作業のファイル ${structure}`
    : `${app} / 構造 ${structure}${info.formula ? ` / ${info.formula} (表示用の繰り返しの前の構造に作業します)` : ""}`;
  document.querySelector('[data-bench="edit"]').hidden = !wb.edit;
  document.querySelector('[data-bench="calc"]').hidden = !calcApps.length;
  if (!dialog.open) dialog.showModal();
  await useApp(bench.mode === "edit" ? app : (calcApps.includes(app) ? app : calcApps[0]));
  selectBenchTab(bench.mode);
}

// この構造で計算できるアプリ: 同じアプリ (構造の番号) と、構造ファイルを受けるアプリ
function calcAppsFor(origin) {
  return Object.entries(state.apps).filter(([name, status]) => status.workbench?.calculate &&
    (status.workbench.source === "file" || (origin.source === "structure" && name === origin.app))).map(([name]) => name);
}

// 作業台で使うアプリを切り替える。構造の渡し方がちがえば、作業の構造ファイルにして渡す
async function useApp(app) {
  const wb = state.apps[app]?.workbench || {};
  let structure = bench.origin.structure;
  if (wb.source === "file" && bench.origin.source === "structure") structure = await structureFileFor(bench.origin.app, structure);
  if (bench.app !== app) Object.assign(bench, {ops: null, kinds: null, potentials: null});   // 一覧はアプリごと
  Object.assign(bench, {app, wb, structure});
}

async function selectBenchTab(mode) {
  bench.mode = mode;
  for (const tab of document.querySelectorAll("[data-bench]")) tab.setAttribute("aria-selected", String(tab.dataset.bench === mode));
  bench.placement?.dispose();
  bench.placement = null;
  const body = $("bench-body");
  body.replaceChildren(h("p", {className: "notice", textContent: "読み込み中…"}));
  try {
    if (mode === "edit") {
      await useApp(bench.origin.app);   // 編集は構造のもとのアプリで
      await showEdit();
    } else {
      if (!calcAppsFor(bench.origin).includes(bench.app)) await useApp(calcAppsFor(bench.origin)[0]);
      await showCalc();
    }
  } catch (error) {
    body.replaceChildren(h("p", {className: "notice error", textContent: error.message}));
  }
}

// ---- 入力欄 -------------------------------------------------------------------------------------

function visible(field, values) {
  if (!field.show_if) return true;
  return Object.entries(field.show_if).every(([key, want]) => {
    const value = values[key];
    return Array.isArray(want) ? want.includes(value) : value === want;
  });
}

// fields から入力欄を作る。read() で表示中の欄の値を返す
function buildForm(fields, context) {
  const box = h("div", {className: "bench-form"});
  const advanced = h("details", {className: "bench-advanced"}, h("summary", {textContent: "詳しい設定"}));
  const rows = [];
  for (const field of fields || []) {
    const input = fieldInput(field, context);
    if (!input) continue;
    const label = h("label", {className: "bench-field"}, h("span", {textContent: field.label}), input,
                    field.unit ? h("small", {textContent: field.unit}) : null);
    const row = h("div", {className: "bench-row"}, label, field.hint ? h("small", {className: "meta", textContent: field.hint}) : null);
    rows.push({field, input, row});
    (field.advanced ? advanced : box).append(row);
  }
  if (advanced.childElementCount > 1) box.append(advanced);
  const values = () => {
    const out = {};
    for (const {field, input} of rows) out[field.key] = readInput(field, input);
    return out;
  };
  const refresh = () => {
    const current = values();
    for (const {field, row} of rows) row.hidden = !visible(field, current);
  };
  box.addEventListener("input", refresh);
  box.addEventListener("change", refresh);
  refresh();
  // 欄に値を入れる (分子を手で置く表示から)。表示の条件も見直す
  const set = (changes) => {
    for (const {field, input} of rows) {
      if (!(field.key in changes)) continue;
      if (field.type === "bool") input.checked = Boolean(changes[field.key]);
      else input.value = changes[field.key] ?? "";
    }
    refresh();
  };
  return {node: box, values, set, read: () => {
    const current = values();
    const out = {};
    for (const {field} of rows) {
      const value = current[field.key];
      if (!visible(field, current) || value === undefined || (value === "" && field.optional)) continue;
      out[field.key] = value;
    }
    return out;
  }};
}

function fieldInput(field, context) {
  const value = field.default;
  if (field.type === "bool") return h("input", {type: "checkbox", checked: Boolean(value)});
  if (field.type === "int" || field.type === "number") {
    const input = h("input", {type: "number", value: value ?? "", step: field.type === "int" ? "1" : "any"});
    if (field.min !== undefined && field.min !== null) input.min = field.min;
    if (field.max !== undefined && field.max !== null) input.max = field.max;
    return input;
  }
  if (field.type === "select") {
    const select = h("select");
    for (const option of field.options || []) select.append(new Option(option.label, option.value));
    if (value !== null && value !== undefined) select.value = value;
    return select;
  }
  if (field.type === "species") {   // 周期表から押して選ぶ
    const choices = elementChoices(field, context.species);
    const button = h("button", {type: "button", className: "element-pick"});
    const show = (symbol) => {
      button.dataset.value = symbol;
      button.textContent = symbol === "*" ? "すべて" : symbol;
    };
    show(value ?? choices[0] ?? "Ni");
    button.addEventListener("click", async () => {
      const picked = await pickElement({current: button.dataset.value, enabled: field.source === "structure" ? choices : null,
                                        highlight: context.species, allowAll: field.allow_all, title: field.label});
      if (picked) {
        show(picked);
        button.dispatchEvent(new Event("change", {bubbles: true}));   // 表示の条件 (show_if) を見直す
      }
    });
    return button;
  }
  if (field.type === "structure") {
    const select = h("select");
    if (field.optional) select.append(new Option("(なし)", ""));
    for (const s of context.structures || []) select.append(new Option(`${s.id}: ${s.name}`, s.id));
    return select;
  }
  if (field.type === "file") {
    return h("span", {className: "meta", textContent: "ファイルは MatSimApp の画面で扱います"});
  }
  const input = h("input", {type: "text", value: value ?? ""});
  if (field.max_length) input.maxLength = field.max_length;
  if (field.pattern) input.pattern = field.pattern;
  return input;
}

function readInput(field, input) {
  if (field.type === "file") return undefined;
  if (field.type === "bool") return input.checked;
  if (field.type === "int") return input.value === "" ? "" : parseInt(input.value, 10);
  if (field.type === "number") return input.value === "" ? "" : Number(input.value);
  if (field.type === "structure") return input.value === "" ? "" : Number(input.value);
  if (field.type === "species") return input.dataset.value;
  return input.value;
}

function result(body, node) {
  const old = body.querySelector(".bench-result");
  if (old) old.replaceWith(node); else body.append(node);
  node.classList.add("bench-result");
}

// ---- 編集 ---------------------------------------------------------------------------------------

async function showEdit() {
  bench.ops ??= (await benchCall("edit_list")).result;
  if (bench.wb.draft) return showDraftEdit();
  return showSingleEdit();
}

// 編集の下書き: 「適用」で編集を重ね、「一つ前に戻す」で取り消し、「保存」で 1 回だけ構造として登録する
async function showDraftEdit() {
  bench.structures = (await benchCall("structures").catch(() => ({result: []}))).result;
  const key = `${bench.origin.app}:${bench.origin.structure}`;
  if (!bench.draft || bench.draft.key !== key) {   // 構造が変わったら下書きを始め直す
    bench.draft = {key, state: (await benchCall("draft_start", {structure_id: bench.origin.structure})).result};
  }
  const body = $("bench-body");
  const select = h("select", {id: "bench-op"});
  for (const group of bench.ops.groups) {
    const optgroup = h("optgroup", {label: group});
    for (const op of bench.ops.ops.filter((o) => o.group === group && o.needs_structure)) optgroup.append(new Option(op.label, op.key));
    if (optgroup.childElementCount) select.append(optgroup);
  }
  select.value = bench.draftOp || (bench.ops.ops.some((o) => o.key === "supercell") ? "supercell" : select.value);
  const viewHolder = h("div");
  const history = h("ol", {className: "draft-steps"});
  const status = h("p", {className: "meta"});
  const note = h("p", {className: "meta"});
  const holder = h("div");
  const name = h("input", {type: "text", maxLength: 80, placeholder: "保存する構造の名前 (空ならおまかせ)"});
  const apply = h("button", {type: "button", textContent: "適用 (下書きに重ねる)"});
  const undo = h("button", {type: "button", className: "quiet", textContent: "一つ前に戻す"});
  const save = h("button", {type: "button", className: "primary", textContent: "保存"});
  const show = (state, warnings = []) => {
    bench.draft.state = state;
    const fresh = h("div");
    viewHolder.replaceChildren(fresh);
    const legendNode = window.mountStructureData?.(fresh, state.view);
    if (legendNode) viewHolder.append(legendNode);
    status.textContent = `下書き: ${state.formula} / ${state.n_atoms} 原子 / ${state.step} 手 (元: ${state.base_name})`;
    history.replaceChildren(...(state.steps.length ? state.steps.map((step) => h("li", {textContent: `${step.label}: ${step.summary}`}))
      : [h("li", {className: "meta empty", textContent: "まだ編集していません。操作を選んで「適用」で重ねます"})]));
    undo.disabled = !state.step;
    save.disabled = !state.step;
    body.querySelector(".bench-result")?.remove();
    if (warnings.length) result(body, h("div", {}, ...warnings.map((w) => h("p", {className: "notice warn", textContent: w}))));
    renderForm();
  };
  const renderForm = () => {
    const op = bench.ops.ops.find((o) => o.key === select.value);
    bench.draftOp = select.value;
    note.textContent = op.note || "";
    const symbols = [...new Set(bench.draft.state.view?.symbols || bench.info.symbols || [])];
    bench.form = buildForm(op.fields, {species: symbols, structures: bench.structures});
    holder.replaceChildren(bench.form.node);
    bench.placement?.dispose();
    bench.placement = null;
    if (op.key === "place_molecule") holder.prepend(placementTools(renderForm));   // 欄より上に (スマホでも見ながら動かせる)
  };
  const run = async (button, work) => {
    button.disabled = true;
    try {
      await work();
    } catch (error) {
      result(body, h("p", {className: "notice error", textContent: error.message}));
    } finally {
      button.disabled = false;
      undo.disabled = !bench.draft.state.step;
      save.disabled = !bench.draft.state.step;
    }
  };
  select.addEventListener("change", renderForm);
  apply.addEventListener("click", () => run(apply, async () => {
    const {result: state} = await benchCall("draft_apply", {draft: bench.draft.state.draft, op: select.value, options: bench.form.read()});
    show(state, state.warnings || []);
  }));
  undo.addEventListener("click", () => run(undo, async () => {
    show((await benchCall("draft_undo", {draft: bench.draft.state.draft})).result);
  }));
  save.addEventListener("click", () => run(save, async () => {
    const conversation = await benchConversation();
    const labels = bench.draft.state.steps.map((step) => step.label).join(" → ");
    const data = await benchCall("draft_save", {draft: bench.draft.state.draft, name: name.value},
                                 {conversation_id: conversation, label: labels});
    bench.draft = null;   // 保存したら下書きはおしまい (新しい構造のカードから続けられる)
    result(body, h("p", {className: "notice", textContent: `${data.summary}。会話に新しい構造が出ます`}));
    setTimeout(() => $("workbench").close(), 900);
  }));
  body.replaceChildren(viewHolder, status, history, h("label", {className: "bench-field"}, h("span", {textContent: "操作"}), select),
                       note, holder, h("div", {className: "bench-buttons"}, undo, apply),
                       h("label", {className: "bench-field"}, h("span", {textContent: "名前"}), name),
                       h("div", {className: "bench-buttons"}, save));
  show(bench.draft.state);
}

// 「分子を置く」: PubChem から探して取り込む・置く分子を影として出し、手で動かす・回す (欄の値と同じものを指す)
function placementTools(renderForm) {
  const view = h("div", {className: "structure-view place-view"});
  const tip = h("p", {className: "meta"});
  const find = h("button", {type: "button", className: "quiet small", textContent: "PubChem から分子を探す"});
  const move = h("button", {type: "button", className: "quiet small", textContent: "動かす"});
  const turn = h("button", {type: "button", className: "quiet small", textContent: "回す"});
  const reset = h("button", {type: "button", className: "quiet small", textContent: "中央に戻す"});
  const setMode = (mode) => {
    move.setAttribute("aria-pressed", String(mode === "move"));
    turn.setAttribute("aria-pressed", String(mode === "rotate"));
    bench.placeMode = mode;
    bench.placement?.setMode(mode);
  };
  move.addEventListener("click", () => setMode("move"));
  turn.addEventListener("click", () => setMode("rotate"));
  reset.addEventListener("click", () => {
    bench.form.set({x: "", y: "", z: "", rx: 0, ry: 0, rz: 0});
    bench.placement?.fromForm();
  });
  find.hidden = !moleculeApp();
  find.addEventListener("click", () => openMolecules({onImported: async (id) => {
    bench.structures = (await benchCall("structures").catch(() => ({result: []}))).result;
    renderForm();
    bench.form.set({molecule: id});
    bench.form.node.dispatchEvent(new Event("change", {bubbles: true}));
  }}));
  const box = h("div", {className: "place-box"},
                h("div", {className: "place-tools"}, find, h("span", {className: "meta", textContent: "手で置く (影をつまむ・ホイールや 2 本指で拡大):"}), move, turn, reset),
                view, tip);
  let shown = null;
  const load = async () => {
    const id = bench.form.values().molecule;
    if (id === shown) return bench.placement?.fromForm();
    shown = id;
    if (id === "" || id === undefined) {
      tip.textContent = "置く分子を選ぶと、ここで影をつまんで動かせます (「回す」で向きを変える)";
      return;
    }
    try {
      const molecule = await api(`views/${encodeURIComponent(bench.origin.app)}/structures/${id}?repeat=1,1,1`);
      if (!bench.placement) {
        bench.placement = window.mountPlacement(view, bench.draft.state.view, bench.form, tip);
        bench.placement.setMode(bench.placeMode || "move");
      }
      bench.placement.setMolecule(molecule);
      if (molecule.n_atoms > 200) tip.textContent += ` / ${molecule.n_atoms} 原子の構造です。分子でなければ選び直してください`;
    } catch (error) {
      tip.className = "notice error";
      tip.textContent = error.message;
    }
  };
  bench.form.node.addEventListener("input", load);
  bench.form.node.addEventListener("change", load);
  setMode(bench.placeMode || "move");
  // 既定は、いちばん新しい PubChem の分子
  const latest = (bench.structures || []).filter((s) => s.source === "pubchem").sort((a, b) => b.id - a.id)[0];
  if (latest) bench.form.set({molecule: latest.id});
  setTimeout(load);
  return box;
}

// 下書きのないアプリ: 1 回の編集をプレビューして保存する
async function showSingleEdit() {
  bench.structures = (await benchCall("structures").catch(() => ({result: []}))).result;
  const body = $("bench-body");
  const select = h("select", {id: "bench-op"});
  for (const group of bench.ops.groups) {
    const optgroup = h("optgroup", {label: group});
    for (const op of bench.ops.ops.filter((o) => o.group === group && o.needs_structure)) optgroup.append(new Option(op.label, op.key));
    if (optgroup.childElementCount) select.append(optgroup);
  }
  select.value = bench.ops.ops.some((o) => o.key === "supercell") ? "supercell" : select.value;
  const note = h("p", {className: "meta"});
  const holder = h("div");
  const name = h("input", {type: "text", maxLength: 80, placeholder: "新しい構造の名前 (空ならおまかせ)"});
  const preview = h("button", {type: "button", className: "quiet", textContent: "プレビュー"});
  const save = h("button", {type: "button", className: "primary", textContent: "保存して表示"});
  const render = () => {
    const op = bench.ops.ops.find((o) => o.key === select.value);
    note.textContent = op.note || "";
    bench.form = buildForm(op.fields, {species: [...new Set(bench.info.symbols || [])], structures: bench.structures});
    holder.replaceChildren(bench.form.node);
    body.querySelector(".bench-result")?.remove();
  };
  const args = () => ({op: select.value, structure_id: bench.structure, options: bench.form.read()});
  select.addEventListener("change", render);
  preview.addEventListener("click", async () => {
    try {
      const {result: data} = await benchCall("edit_preview", args());
      if (!name.value && data.default_name) name.placeholder = data.default_name;
      result(body, h("div", {className: "notice"},
        h("b", {textContent: `${data.summary || ""} → ${data.formula} (${data.n_atoms} 原子)`}),
        ...(data.warnings || []).map((w) => h("p", {className: "notice warn", textContent: w})),
        data.over_guideline ? h("p", {className: "notice warn", textContent: "原子数が目安を超えています"}) : null));
    } catch (error) {
      result(body, h("p", {className: "notice error", textContent: error.message}));
    }
  });
  save.addEventListener("click", async () => {
    const op = bench.ops.ops.find((o) => o.key === select.value);
    save.disabled = true;
    try {
      const conversation = await benchConversation();
      const data = await benchCall("edit_save", {...args(), name: name.value}, {conversation_id: conversation, label: op.label});
      result(body, h("p", {className: "notice", textContent: `${data.summary}。会話に新しい構造が出ます`}));
      setTimeout(() => $("workbench").close(), 900);
    } catch (error) {
      result(body, h("p", {className: "notice error", textContent: error.message}));
    } finally {
      save.disabled = false;
    }
  });
  body.replaceChildren(h("label", {className: "bench-field"}, h("span", {textContent: "操作"}), select), note, holder,
                       h("label", {className: "bench-field"}, h("span", {textContent: "名前"}), name),
                       h("div", {className: "bench-buttons"}, preview, save));
  render();
}

// ---- 計算 ---------------------------------------------------------------------------------------

async function showCalc() {
  bench.kinds ??= (await benchCall("calc_list")).result;
  if (bench.wb.potentials) bench.potentials ??= (await benchCall("potentials")).result.filter((p) => p.available !== false);
  const body = $("bench-body");
  // 計算に使うアプリ (つないだツールのうち、この構造を受け取れるもの)
  const apps = calcAppsFor(bench.origin);
  const appSelect = h("select");
  for (const name of apps) appSelect.append(new Option(name, name));
  appSelect.value = bench.app;
  appSelect.addEventListener("change", async () => {
    body.replaceChildren(h("p", {className: "notice", textContent: "読み込み中…"}));
    try {
      await useApp(appSelect.value);
      await showCalc();
    } catch (error) {
      body.replaceChildren(h("p", {className: "notice error", textContent: error.message}));
    }
  });
  const appRow = apps.length > 1 ? h("label", {className: "bench-field"}, h("span", {textContent: "計算に使うアプリ"}), appSelect) : null;
  const fileNote = bench.wb.source === "file" && bench.origin.source === "structure"
    ? h("p", {className: "meta", textContent: `作業の ${bench.structure} を渡します`}) : null;
  const kind = h("select");
  const groups = [...new Set(bench.kinds.map((k) => k.group))];
  for (const group of groups) {
    const optgroup = h("optgroup", {label: group});
    for (const k of bench.kinds.filter((x) => x.group === group)) optgroup.append(new Option(k.label, k.key));
    kind.append(optgroup);
  }
  if (bench.kinds.some((k) => k.key === "static")) kind.value = "static";
  const potential = h("select");
  for (const p of bench.potentials || []) potential.append(new Option(p.name, p.id));
  const potentialRow = bench.wb.potentials ? h("label", {className: "bench-field"}, h("span", {textContent: "ポテンシャル"}), potential) : null;
  const repeat = h("input", {type: "text", placeholder: "自動 (厚み 12 Å 以上)", maxLength: 20, pattern: "\\d+\\s*,\\s*\\d+\\s*,\\s*\\d+"});
  const repeatRow = h("label", {className: "bench-field"}, h("span", {textContent: "スーパーセル"}), repeat, h("small", {textContent: "例: 4,4,3"}));
  const traj = h("input", {type: "number", min: 1, step: 1, placeholder: "自動 (全体で約 50 コマ)"});
  const trajRow = bench.wb.traj_arg ? h("label", {className: "bench-field"}, h("span", {textContent: "構造を記録する間隔"}), traj,
                                        h("small", {textContent: "ステップ"})) : null;
  const note = h("p", {className: "meta"});
  const holder = h("div");
  const estimate = h("button", {type: "button", className: "quiet", textContent: "見積もり"});
  const submit = h("button", {type: "button", className: "primary", textContent: "投入"});
  let described = null;
  let stages = null;
  const render = async () => {
    body.querySelector(".bench-result")?.remove();
    described = (await benchCall("calc_describe", {[bench.wb.kind_arg]: kind.value})).result;
    note.textContent = described.note || "";
    repeatRow.hidden = !bench.wb.repeat || Boolean(described.no_repeat);
    if (!described.fields && described.stages_example) {
      stages = stageEditor(described.stages_example);
      bench.form = null;
      holder.replaceChildren(stages.node);
      if (trajRow) trajRow.hidden = false;
    } else {
      stages = null;
      if (trajRow) trajRow.hidden = true;
      bench.form = buildForm(described.fields, {species: [...new Set(bench.info.symbols || [])], structures: []});
      holder.replaceChildren(bench.form.node);
    }
  };
  const args = () => {
    const out = {[bench.wb.kind_arg]: kind.value, [bench.wb.structure_arg]: bench.structure};
    if (bench.wb.potentials) out.potential_id = Number(potential.value);
    if (stages) {
      out.stages = stages.read();
      if (bench.wb.traj_arg) out[bench.wb.traj_arg] = trajInterval(out.stages, traj.value);   // 構造の時間変化を見られるように
    } else {
      out.options = bench.form.read();
    }
    const r = repeat.value.trim();
    if (r && bench.wb.repeat && !described.no_repeat) out.repeat = r.split(/\s*,\s*/).map(Number);
    return out;
  };
  kind.addEventListener("change", () => render().catch((error) => result(body, h("p", {className: "notice error", textContent: error.message}))));
  estimate.addEventListener("click", async () => {
    try {
      const {result: data} = await benchCall("calc_preview", args());
      const atoms = data.n_atoms ?? data.natoms;
      // 所要時間の目安があれば出す (第一原理計算など、見積もれないアプリもある)
      const time = typeof data.estimated_seconds === "number"
        ? ` / ${data.total_steps ?? "?"} ステップ / 目安 ${data.estimated_seconds < 60 ? "1 分未満" : `${(data.estimated_seconds / 60).toFixed(1)} 分`}`
        : " / 所要時間の目安なし (投入は毎回確認します)";
      result(body, h("div", {className: "notice"},
        h("b", {textContent: `${data.formula ?? ""} / ${atoms ?? "?"} 原子${data.spacegroup ? ` / ${data.spacegroup}` : ""}${time}`}),
        ...(data.notes || []).map((n) => h("p", {className: "notice warn", textContent: n})),
        data.run_in ? h("details", {}, h("summary", {textContent: "run.in"}), h("pre", {textContent: data.run_in})) : null,
        data.info ? h("details", {}, h("summary", {textContent: "構造と推奨の条件"}), h("pre", {textContent: data.info})) : null,
        data.command ? h("details", {}, h("summary", {textContent: "実行するコマンド"}), h("pre", {textContent: data.command})) : null));
    } catch (error) {
      result(body, h("p", {className: "notice error", textContent: error.message}));
    }
  });
  submit.addEventListener("click", async () => {
    const label = bench.kinds.find((k) => k.key === kind.value)?.label || kind.value;
    submit.disabled = true;
    result(body, h("p", {className: "notice", textContent: "投入しています… 長い計算は会話の確認のカードで「実行する」を押してください"}));
    try {
      const conversation = await benchConversation();
      const data = await benchCall("calc_submit", args(), {conversation_id: conversation, label});
      result(body, h("p", {className: "notice", textContent: `${data.summary}。終わると会話に知らせます`}));
      setTimeout(() => $("workbench").close(), 1500);
    } catch (error) {
      result(body, h("p", {className: "notice error", textContent: error.message}));
    } finally {
      submit.disabled = false;
    }
  });
  body.replaceChildren(...[appRow, fileNote, h("label", {className: "bench-field"}, h("span", {textContent: "計算"}), kind), note,
                           potentialRow, repeatRow, holder, trajRow, h("div", {className: "bench-buttons"}, estimate, submit)].filter(Boolean));
  await render();
}

// MD の構造を記録する間隔。空なら、すべての段階のステップの合計で約 50 コマになるようにする
function trajInterval(stages, typed) {
  const given = parseInt(typed, 10);
  if (given > 0) return given;
  const total = stages.reduce((sum, stage) => sum + (Number(stage.steps) || 0), 0);
  return Math.max(1, Math.round(total / 50));
}

// MD の段階 (アンサンブル・ステップ・温度・終わりの温度・圧力) を表で入れる
function stageEditor(example) {
  const table = h("div", {className: "stages"});
  const rows = [];
  const add = (stage) => {
    const ensemble = h("select");
    for (const e of ["nvt", "npt", "nve"]) ensemble.append(new Option(e.toUpperCase(), e));
    ensemble.value = stage.ensemble || "nvt";
    const number = (value, placeholder) => h("input", {type: "number", value: value ?? "", placeholder, step: "any"});
    const cells = {ensemble, steps: number(stage.steps, "ステップ"), temperature: number(stage.temperature, "温度 K"),
                   temperature_end: number(stage.temperature_end, "終わり K"), pressure: number(stage.pressure, "圧力 GPa")};
    const remove = h("button", {type: "button", className: "quiet", textContent: "✕"});
    const row = h("div", {className: "stage"}, ...Object.values(cells), remove);
    const entry = {cells, row};
    remove.addEventListener("click", () => { rows.splice(rows.indexOf(entry), 1); row.remove(); });
    rows.push(entry);
    table.append(row);
  };
  example.forEach(add);
  const more = h("button", {type: "button", className: "quiet small", textContent: "＋ 段階を足す"});
  more.addEventListener("click", () => add({ensemble: "nvt", steps: 10000, temperature: 300}));
  return {
    node: h("div", {}, h("p", {className: "meta", textContent: "段階ごとに: アンサンブル / ステップ / 温度 / 終わりの温度 (空なら一定) / 圧力 (NPT)"}), table, more),
    read: () => rows.map(({cells}) => {
      const stage = {ensemble: cells.ensemble.value};
      for (const key of ["steps", "temperature", "temperature_end", "pressure"]) {
        if (cells[key].value !== "") stage[key] = Number(cells[key].value);
      }
      if (stage.ensemble !== "npt") delete stage.pressure;
      return stage;
    }),
  };
}

// 保存と投入は会話に残す。会話を開いていなければ作る
async function benchConversation() {
  if (state.conversation === null && !(await newChat())) throw new Error("出力先を決めると保存・投入できます");
  return state.conversation;
}

// app.js より先に読むので、ここでは $ を使わない
for (const tab of document.querySelectorAll("[data-bench]")) tab.addEventListener("click", () => selectBenchTab(tab.dataset.bench));
document.getElementById("bench-close").addEventListener("click", () => document.getElementById("workbench").close());
document.getElementById("workbench").addEventListener("close", () => {   // 手で置く表示の WebGL を返す
  bench.placement?.dispose();
  bench.placement = null;
});
window.openWorkbench = openWorkbench;
