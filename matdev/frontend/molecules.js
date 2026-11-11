"use strict";

// ---- 構造を取り込む: 結晶は Materials Project (OPTIMADE)、分子は PubChem から ---------------------
// 探すのは読むだけの道具を直接、取り込みは作業台の保存と同じく関門を通す (会話に 3D のカードと「⚙」が残る)。
// 分子は全方位に真空を空けた箱に入れる。作業台の「分子を置く」から開いたときは、取り込んだ分子をそのまま置く分子に選ぶ (onImported)。

const molecules = {onImported: null};

// 結晶を取り込めるアプリ (設定の workbench.crystals があるもの)
function crystalApp() {
  return Object.entries(state.apps || {}).find(([, status]) => status.workbench?.crystals)?.[0] || null;
}

function crystalCall(action, args, extra = {}) {
  return api(`workbench/${encodeURIComponent(crystalApp())}`,
             {method: "POST", body: JSON.stringify({action, args, conversation_id: state.conversation, ...extra})});
}

function showImportTab(kind) {
  for (const tab of document.querySelectorAll("[data-import]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.import === kind));
  }
  document.getElementById("import-crystal").hidden = kind !== "crystal";
  document.getElementById("import-molecule").hidden = kind !== "molecule";
  document.getElementById(kind === "crystal" ? "crystal-query" : "molecule-query").focus();
}

async function searchCrystals() {
  const query = document.getElementById("crystal-query").value.trim();
  const list = document.getElementById("crystal-results");
  if (!query) return;
  list.replaceChildren(h("p", {className: "notice", textContent: "Materials Project で探しています…"}));
  try {
    const {result: found} = await crystalCall("crystal_search", {query});
    list.replaceChildren(...[].concat(found).map(crystalRow));   // 1 件の一覧は 1 つの値で届く
  } catch (error) {
    list.replaceChildren(h("p", {className: "notice error", textContent: error.message}));
  }
}

function crystalRow(found) {
  const facts = [found.spacegroup ? `空間群 ${found.spacegroup}` : null, `${found.n_atoms ?? "?"} 原子`,
                 found.e_above_hull !== null && found.e_above_hull !== undefined ? `凸包から ${found.e_above_hull.toFixed(3)} eV/原子` : null]
    .filter(Boolean);
  const button = h("button", {type: "button", className: "small", textContent: "取り込む", disabled: !found.importable});
  const row = h("div", {className: "molecule-row"},
                h("div", {}, h("b", {textContent: found.formula || "?"}),
                  h("small", {className: "meta", textContent: ` ${found.id}`}),
                  h("small", {className: "meta chips-line", textContent: facts.join(" ・ ")}),
                  found.importable ? null : h("small", {className: "bad", textContent: found.problem || "取り込めません"})),
                button);
  button.addEventListener("click", async () => {
    button.disabled = true;
    const note = document.getElementById("crystal-note");
    note.className = "notice";
    note.textContent = `${found.formula} (${found.id}) を取り込んでいます…`;
    try {
      const conversation = await benchConversation();
      const label = `${found.formula}${found.spacegroup ? ` ${found.spacegroup.split(" ")[0]}` : ""}`;
      const data = await crystalCall("crystal_import",
        {entry_id: found.id, conventional: document.getElementById("crystal-conventional").checked},
        {conversation_id: conversation, label});
      note.textContent = `${data.summary}。会話に 3D のカードが出ます (${data.result.n_atoms} 原子。編集・計算・その場で MD に進めます)`;
    } catch (error) {
      note.className = "notice error";
      note.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  return row;
}

// 分子を取り込めるアプリ (設定の workbench.molecules があるもの)
function moleculeApp() {
  return Object.entries(state.apps || {}).find(([, status]) => status.workbench?.molecules)?.[0] || null;
}

function moleculeCall(action, args, extra = {}) {
  return api(`workbench/${encodeURIComponent(moleculeApp())}`,
             {method: "POST", body: JSON.stringify({action, args, conversation_id: state.conversation, ...extra})});
}

// kind: "crystal" か "molecule"。作業台の「分子を置く」からは分子
function openMolecules(options = {}) {
  molecules.onImported = options.onImported || null;
  const dialog = document.getElementById("molecules");
  for (const tab of document.querySelectorAll("[data-import]")) {
    tab.hidden = tab.dataset.import === "crystal" ? !crystalApp() : !moleculeApp();
  }
  if (!dialog.open) dialog.showModal();
  const kind = options.kind || (options.onImported || !crystalApp() ? "molecule" : "crystal");
  showImportTab(kind);
}

async function searchMolecules() {
  const query = document.getElementById("molecule-query").value.trim();
  const list = document.getElementById("molecule-results");
  if (!query) return;
  list.replaceChildren(h("p", {className: "notice", textContent: "PubChem で探しています…"}));
  try {
    const {result: found} = await moleculeCall("molecule_search", {query});
    list.replaceChildren(...[].concat(found).map(moleculeRow));   // 1 件の一覧は 1 つの値で届く
  } catch (error) {
    list.replaceChildren(h("p", {className: "notice error", textContent: error.message}));
  }
}

function moleculeRow(found) {
  const button = h("button", {type: "button", className: "small", textContent: "取り込む"});
  const row = h("div", {className: "molecule-row"},
                h("div", {}, h("b", {textContent: found.name}),
                  h("small", {className: "meta", textContent:
                    ` ${found.formula || ""}${found.weight ? ` / ${found.weight.toFixed(2)} g/mol` : ""} / CID ${found.cid}`}),
                  found.iupac && found.iupac.toLowerCase() !== found.name.toLowerCase()
                    ? h("small", {className: "meta molecule-iupac", textContent: found.iupac}) : null),
                button);
  button.addEventListener("click", async () => {
    button.disabled = true;
    const note = document.getElementById("molecule-note");
    note.className = "notice";
    note.textContent = `${found.name} の 3D 配座を取り込んでいます…`;
    try {
      const vacuum = Number(document.getElementById("molecule-vacuum").value) || 10;
      const conversation = await benchConversation();
      const data = await moleculeCall("molecule_import", {cid: found.cid, vacuum, name: found.name},
                                      {conversation_id: conversation, label: found.name});
      note.textContent = `${data.summary}。会話に 3D のカードが出ます (編集・計算・その場で MD に進めます)`;
      if (molecules.onImported) {
        const done = molecules.onImported;
        molecules.onImported = null;
        document.getElementById("molecules").close();
        await done(data.result.id);
      }
    } catch (error) {
      note.className = "notice error";
      note.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  });
  return row;
}

// app.js より先に読むので、ここでは $ を使わない
document.getElementById("open-molecules").addEventListener("click", () => openMolecules());
document.getElementById("molecule-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchMolecules();
});
document.getElementById("molecules-close").addEventListener("click", () => document.getElementById("molecules").close());
document.getElementById("crystal-form").addEventListener("submit", (event) => {
  event.preventDefault();
  searchCrystals();
});
for (const tab of document.querySelectorAll("[data-import]")) {
  tab.addEventListener("click", () => {
    showImportTab(tab.dataset.import);
  });
}
