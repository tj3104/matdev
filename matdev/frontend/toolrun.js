"use strict";

// ---- 道具: チャットなしで、つないだアプリの道具を画面から使う ----------------------------------------------
// 入力欄は道具の入力の定義 (JSON Schema) から作る。実行はサーバーの関門を通る (確認・見積もり・見張りはジャービスと同じ)。
// やったことと結果は会話に「⚙」で残り、次にジャービスに頼むときにも伝わる。

const toolrun = {list: null, files: []};

async function openTools() {
  toolrun.list ??= await api("tools");
  const dialog = $("tools");
  const apps = [...new Set(toolrun.list.map((t) => t.app))];
  const appSelect = $("tool-app");
  appSelect.replaceChildren(...apps.map((a) => new Option(a, a)));
  if (!dialog.open) dialog.showModal();
  await loadWorkFiles();
  showToolList();
}

// 作業の中のパスを受ける欄の候補 (一番上と、よく使うディレクトリ)
async function loadWorkFiles() {
  toolrun.files = [];
  if (!works.active) return;
  for (const directory of ["", "structures"]) {
    const listing = await api(`works/${works.active.id}/files?directory=${directory}`).catch(() => ({entries: []}));
    toolrun.files.push(...listing.entries.filter((e) => !e.dir).map((e) => e.path));
  }
}

function showToolList() {
  const app = $("tool-app").value;
  const select = $("tool-name");
  const tools = toolrun.list.filter((t) => t.app === app);
  select.replaceChildren(...tools.map((t) => new Option(`${t.tool}${t.read_only ? "  (見るだけ)" : t.destructive ? "  (消す・止める)" : ""}`, t.name)));
  showToolForm();
}

function schemaOf(property) {
  // anyOf [X, null] は「空でもよい X」として扱う
  if (property.anyOf) {
    const main = property.anyOf.find((s) => s.type !== "null") || {};
    return {...main, ...property, anyOf: undefined, optional: property.anyOf.some((s) => s.type === "null")};
  }
  return property;
}

function toolField(name, property, required, pathArg) {
  const schema = schemaOf(property);
  const label = el("label", "bench-field");
  label.append(el("span", "", `${schema.title || name}${required.includes(name) ? " *" : ""}`));
  let input;
  const type = Array.isArray(schema.type) ? schema.type.find((t) => t !== "null") : schema.type;
  if (schema.enum) {
    input = document.createElement("select");
    if (!required.includes(name)) input.append(new Option("(指定しない)", ""));
    for (const value of schema.enum) input.append(new Option(String(value), String(value)));
  } else if (type === "boolean") {
    input = Object.assign(document.createElement("input"), {type: "checkbox", checked: Boolean(schema.default)});
  } else if (type === "integer" || type === "number") {
    input = Object.assign(document.createElement("input"), {type: "number", step: type === "integer" ? "1" : "any"});
    if (schema.default !== undefined && schema.default !== null) input.value = schema.default;
  } else if (type === "array" && ["integer", "number", "string"].includes(schema.items?.type || schema.prefixItems?.[0]?.type)) {
    input = Object.assign(document.createElement("input"), {placeholder: "カンマ区切り (例: 2,2,2)"});
    if (Array.isArray(schema.default)) input.value = schema.default.join(",");
  } else if (type === "object" || type === "array" || !type) {
    input = Object.assign(document.createElement("textarea"), {rows: 2, placeholder: type === "array" ? "[...] (JSON)" : '{"名前": 値} (JSON)'});
    if (schema.default !== undefined && schema.default !== null) input.value = JSON.stringify(schema.default);
  } else {
    input = Object.assign(document.createElement("input"), {type: "text"});
    if (schema.default !== undefined && schema.default !== null) input.value = schema.default;
    if (pathArg) {   // 作業の中のファイル (作業からの相対パス)
      const list = Object.assign(document.createElement("datalist"), {id: `files-${name}`});
      list.append(...toolrun.files.map((path) => new Option(path, path)));
      input.setAttribute("list", list.id);
      input.placeholder = "作業のファイル (例: structures/xxx.cif)";
      label.append(list);
    }
  }
  input.dataset.name = name;
  input.dataset.kind = schema.enum ? "enum" : type === "array" && input.tagName === "INPUT" ? "list" : type || "json";
  input.dataset.items = schema.items?.type || schema.prefixItems?.[0]?.type || "";
  label.append(input);
  const row = el("div", "bench-row");
  row.append(label);
  if (schema.description) row.append(el("small", "meta", schema.description));
  return row;
}

function readToolForm(form) {
  const args = {};
  for (const input of form.querySelectorAll("[data-name]")) {
    const {name, kind, items} = input.dataset;
    if (kind === "boolean") {
      args[name] = input.checked;
      continue;
    }
    const raw = input.value.trim();
    if (raw === "") continue;   // 空の欄は道具の既定にまかせる
    if (kind === "integer") args[name] = parseInt(raw, 10);
    else if (kind === "number") args[name] = Number(raw);
    else if (kind === "list") args[name] = raw.split(/[\s,]+/).map((v) => (items === "string" ? v : Number(v)));
    else if (kind === "object" || kind === "array" || kind === "json") {
      try {
        args[name] = JSON.parse(raw);
      } catch {
        throw new Error(`${name} は JSON で書いてください`);
      }
    } else if (kind === "enum") {
      const number = Number(raw);
      args[name] = raw !== "" && !Number.isNaN(number) && String(number) === raw ? number : raw;
    } else args[name] = raw;
  }
  return args;
}

function showToolForm() {
  const tool = toolrun.list.find((t) => t.name === $("tool-name").value);
  const body = $("tool-body");
  body.replaceChildren();
  if (!tool) return;
  body.append(el("p", "meta", tool.description || ""));
  const form = el("div", "bench-form");
  const schema = tool.input_schema || {};
  const required = schema.required || [];
  for (const [name, property] of Object.entries(schema.properties || {})) {
    form.append(toolField(name, property, required, tool.work_paths.includes(name)));
  }
  if (!form.childElementCount) form.append(el("p", "meta", "入力はありません"));
  const run = el("button", "primary", tool.read_only ? "見る" : "実行");
  run.type = "button";
  const output = el("div", "tool-output");
  run.addEventListener("click", async () => {
    run.disabled = true;
    output.replaceChildren(el("p", "notice", tool.read_only ? "読み込み中…" : "実行しています… 確認が要るものは会話の確認のカードで答えてください"));
    try {
      const conversation = await benchConversation();
      const data = await api(`conversations/${conversation}/tools`, {method: "POST", body: JSON.stringify({name: tool.name, args: readToolForm(form)})});
      output.replaceChildren(el("p", data.ok ? "notice" : "notice error", data.ok ? "会話にも残しました" : "失敗しました (会話にも残しました)"),
                             el("pre", "file-text", prettyResult(data.result)));
    } catch (error) {
      output.replaceChildren(el("p", "notice error", error.message));
    } finally {
      run.disabled = false;
    }
  });
  const buttons = el("div", "bench-buttons");
  buttons.append(run);
  body.append(form, buttons, output);
}

function prettyResult(text) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

// app.js より先に読むので、ここでは $ を使わない
document.getElementById("open-tools").addEventListener("click", () => openTools().catch((error) => alert(error.message)));
document.getElementById("tool-app").addEventListener("change", () => showToolList());
document.getElementById("tool-name").addEventListener("change", () => showToolForm());
document.getElementById("tools-close").addEventListener("click", () => document.getElementById("tools").close());
