"use strict";

// ---- 資料のパネル -----------------------------------------------------------------------
// この会話: ジャービスが道具で読んだ・作ったもの (ジョブ・道具の結果) と書いたノート
// これまで: すべての会話 (Claude Code の入口を含む) の資料。ノート: ノートの一覧

const panel = {tab: "chat", version: 0, timer: null};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function when(stamp) {
  const date = new Date(stamp);
  if (Number.isNaN(date.getTime())) return stamp.slice(0, 10);
  return date.toLocaleString("ja-JP", {month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit"});
}

function section(title, count) {
  const box = el("section", "panel-section");
  box.append(el("p", "caption", count === undefined ? title : `${title} ${count}`));
  return box;
}

function empty(text) {
  return el("p", "notice", text);
}

function schedulePanel() {
  clearTimeout(panel.timer);
  panel.timer = setTimeout(() => refreshPanel().catch(() => {}), 800);
}

async function refreshPanel() {
  const version = ++panel.version;
  const body = $("panel-body");
  const query = $("panel-query").value;
  let parts;
  if (panel.tab === "chat") {
    if (state.conversation === null) {
      parts = [empty("会話を選ぶと、その会話で使った資料が出ます")];
    } else {
      const data = await api(`conversations/${state.conversation}/materials`);
      parts = [structureSection(data.structures, false), jobSection(data.jobs, false), noteSection(data.notes, "この会話のノート"),
               callSection(data.calls, false)];
      if (!data.jobs.length && !data.calls.length && !data.notes.length) {
        parts = [empty("この会話ではまだ道具を使っていません")];
      } else if (data.calls.length) {
        parts.push(clearButton());
      }
    }
  } else if (panel.tab === "work") {
    parts = await workSection();
  } else if (panel.tab === "all") {
    const data = await api(`materials?q=${encodeURIComponent(query)}`);
    parts = [structureSection(data.structures, true), jobSection(data.jobs, true), callSection(data.calls, true)];
  } else {
    const notes = await api(`notes?q=${encodeURIComponent(query)}`);
    parts = [noteSection(notes, "ノート", `ノートはまだありません。計算の結果を報告するときに${state.name}が書きます`)];
  }
  if (version !== panel.version) return;   // 後から始めた読み込みを優先する
  body.replaceChildren(...parts.filter(Boolean));
}

// ジョブ: グラフとアプリの画面へのリンク
function jobSection(jobs, withConversation) {
  if (!jobs.length && !withConversation) return null;
  const box = section("ジョブ", jobs.length);
  if (!jobs.length) box.append(empty("ジョブはありません"));
  for (const job of jobs) {
    const card = el("article", "card job");
    const head = el("div", "card-head");
    head.append(el("b", "", `${job.app} / ジョブ ${job.job_id}`), el("small", "", when(job.last)));
    card.append(head, el("small", "meta", `使った道具: ${job.tools.join(", ")}`), ...[saveButton(job.app, "job", job.job_id)].filter(Boolean));
    if (withConversation) card.append(conversationLink(job.conversation_id, job.conversation, job.conversation_deleted));
    card.append(...jobViewer(job.app, [job.job_id]));
    box.append(card);
  }
  return box;
}

// 構造: 押すと 3D で出す (WebGL を使いすぎないよう、最初は出さない)
function structureSection(structures, withConversation) {
  if (!structures?.length) return null;
  const box = section("構造", structures.length);
  for (const entry of structures) {
    const card = el("article", "card");
    const head = el("div", "card-head");
    head.append(el("b", "", `${entry.app} / 構造 ${entry.structure_id}`), el("small", "", when(entry.last)));
    card.append(head, el("small", "meta", `使った道具: ${entry.tools.join(", ")}`), ...[saveButton(entry.app, "structure", entry.structure_id)].filter(Boolean));
    if (withConversation) card.append(conversationLink(entry.conversation_id, entry.conversation, entry.conversation_deleted));
    const holder = el("div");
    const show = el("button", "quiet small", "3D で見る");
    show.type = "button";
    show.addEventListener("click", () => {
      show.remove();
      window.mountStructure?.(holder, entry.app, entry.structure_id);
    });
    card.append(show, holder);
    box.append(card);
  }
  return box;
}

// ジョブの結果・構造を、いまの作業のディレクトリに置く
function saveButton(app, kind, id) {
  if (!state.apps[app]?.export?.includes(kind)) return null;   // 結果を最初から作業に置くアプリ (ezcal など) には要らない
  const button = el("button", "quiet small", "作業に置く");
  button.type = "button";
  button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      const {work} = await api("works/save", {method: "POST", body: JSON.stringify({app, kind, id: String(id), conversation_id: state.conversation})});
      button.textContent = `「${work.name}」に置きました`;
    } catch (error) {
      button.textContent = error.message;
      button.disabled = false;
    }
  });
  return button;
}

function jobViewer(appName, jobIds) {
  const app = state.apps[appName];
  if (!app || !(app.graphs || app.link) || !jobIds.length) return [];
  const viewer = el("div", "viewer");
  const buttons = el("div", "job-buttons");
  for (const jobId of jobIds) {
    const button = el("button", "quiet", app.graphs ? `ジョブ ${jobId} のグラフ` : `ジョブ ${jobId} を開く`);
    button.type = "button";
    button.addEventListener("click", () => openJob(viewer, buttons, button, appName, jobId));
    buttons.append(button);
  }
  return [buttons, viewer];
}

function conversationLink(conversationId, title, deleted) {
  if (conversationId === null) return el("small", "meta", "Claude Code の入口から");
  if (deleted) return el("small", "meta", "消した会話から");
  const link = el("button", "link", `会話: ${title || conversationId}`);
  link.type = "button";
  link.addEventListener("click", async () => {
    togglePanel(false);
    await openChat(conversationId);
  });
  return link;
}

// 道具の結果: 押すと引数と結果の全文を出す
function callSection(calls, withConversation) {
  if (!calls.length) return null;
  const box = section("道具の結果", calls.length);
  for (const call of calls) {
    const card = el("article", "card call");
    const toggle = el("button", "call-head");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    const name = el("span", "tool-name", `${call.app}__${call.tool}`);
    toggle.append(name, el("small", "", when(call.created_at)), el("span", "call-args", call.summary || "(引数なし)"));
    const detail = el("div", "call-detail");
    detail.hidden = true;
    toggle.addEventListener("click", () => openCall(call.id, toggle, detail));
    card.append(toggle);
    if (withConversation) card.append(conversationLink(call.conversation_id, call.conversation, call.conversation_deleted));
    card.append(detail);
    box.append(card);
  }
  return box;
}

async function openCall(id, toggle, detail) {
  const open = toggle.getAttribute("aria-expanded") !== "true";
  toggle.setAttribute("aria-expanded", String(open));
  detail.hidden = !open;
  if (!open || detail.childElementCount) return;
  detail.append(empty("読み込み中…"));
  try {
    const data = await api(`materials/${id}`);
    detail.replaceChildren(el("p", "caption", "引数"), el("pre", "", JSON.stringify(data.args, null, 2)),
                           el("p", "caption", `結果 (${data.size.toLocaleString()} 文字)`), el("pre", "result", data.result));
    if (data.jobs.length) detail.append(...jobViewer(data.app, data.jobs));
    const remove = el("button", "danger small", "この資料を消す");
    remove.type = "button";
    remove.addEventListener("click", async () => {
      if (!confirm("この資料 (道具の呼び出しと結果) を消します")) return;
      await api(`materials/${id}`, {method: "DELETE"}).catch((error) => alert(error.message));
      refreshPanel().catch(() => {});
    });
    detail.append(remove);
  } catch (error) {
    detail.replaceChildren(el("p", "notice error", error.message));
  }
}

function clearButton() {
  const button = el("button", "danger small", "この会話の資料をすべて消す");
  button.type = "button";
  button.addEventListener("click", async () => {
    if (!confirm("この会話の資料 (道具の呼び出しと結果) をすべて消します。ノートは残ります")) return;
    await api(`conversations/${state.conversation}/materials`, {method: "DELETE"}).catch((error) => alert(error.message));
    refreshPanel().catch(() => {});
  });
  return button;
}

function noteSection(notes, title, emptyText) {
  if (!notes.length) return emptyText ? empty(emptyText) : null;
  const box = section(title, notes.length);
  for (const note of notes) box.append(noteCard(note));
  return box;
}

function noteCard(note) {
  const card = el("article", "card note");
  const head = el("div", "card-head");
  head.append(el("b", "", note.title), el("small", "", note.created_at.slice(0, 10)));
  const where = note.app ? `${note.app} の` : "";
  card.append(head, el("small", "meta", `${where}ジョブ ${note.job_ids.join(", ") || "なし"}`));
  const fields = document.createElement("dl");
  for (const [label, value] of [["目的", note.purpose], ["条件", note.conditions], ["分かったこと", note.findings]]) {
    fields.append(el("dt", "", label), el("dd", "", value));
  }
  card.append(fields, ...jobViewer(note.app, note.job_ids));
  // ノートに残した構造は、押すと 3D で出す
  for (const structureId of note.structure_ids || []) {
    if (!state.apps[note.app]?.structures) break;
    const holder = el("div");
    const show = el("button", "quiet small", `構造 ${structureId} を 3D で見る`);
    show.type = "button";
    show.addEventListener("click", () => {
      show.remove();
      window.mountStructure?.(holder, note.app, structureId);
    });
    card.append(show, holder);
  }
  return card;
}

// app.js より先に読むので、ここでは $ を使わない
for (const tab of document.querySelectorAll("[data-tab]")) {
  tab.addEventListener("click", () => {
    panel.tab = tab.dataset.tab;
    for (const other of document.querySelectorAll("[data-tab]")) other.setAttribute("aria-selected", String(other === tab));
    $("panel-query").hidden = !["all", "notes"].includes(panel.tab);
    $("panel-body").replaceChildren();
    refreshPanel().catch((error) => $("panel-body").replaceChildren(el("p", "notice error", error.message)));
  });
}

document.getElementById("panel-query").addEventListener("input", () => {
  clearTimeout(panel.timer);
  panel.timer = setTimeout(() => refreshPanel().catch(() => {}), 250);
});
