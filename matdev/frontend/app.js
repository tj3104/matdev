"use strict";

const $ = (id) => document.getElementById(id);
const state = {conversation: null, source: null, draft: null, draftText: "", busy: false, apps: {}, name: "ジャービス"};

async function api(path, options = {}) {
  const response = await fetch(`/api/${path}`, {
    ...options,
    headers: {"X-Requested-With": "matdev",
              ...(options.body && !(options.body instanceof FormData) ? {"Content-Type": "application/json"} : {})},
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    // 道具の失敗の頭に付く英語 ("Error executing tool xxx:") は外して、理由だけを出す
    const detail = typeof data.detail === "string" ? data.detail.replace(/^Error executing tool \S+:\s*/, "") : "入力を確認してください";
    const error = new Error(detail);
    error.status = response.status;
    throw error;
  }
  return data;
}

const post = (path, body) => api(path, {method: "POST", body: body === undefined ? undefined : JSON.stringify(body)});

// ---- ログイン ---------------------------------------------------------------------------

async function boot() {
  const status = await api("status");
  if (!status.user) {
    showAuth(status.needs_setup);
    return;
  }
  $("auth").hidden = true;
  $("app").hidden = false;
  const problems = [];
  if (!status.llm_ready) problems.push(`.env に ${status.llm_key_env} を設定して起動し直してください (LLM: ${status.llm})`);
  if (status.limit_usd === null) problems.push(".env に MATDEV_MONTHLY_LIMIT_USD を設定して起動し直してください");
  state.apps = Object.fromEntries((status.apps || []).map((app) => [app.name, app]));
  $("open-molecules").hidden = !moleculeApp() && !crystalApp();
  for (const app of status.apps || []) {
    if (app.error) problems.push(`${app.name} に接続できません: ${app.error}`);
  }
  renderSystems(status);
  showName(status.assistant_name);
  showWork(status.work, status.work_roots);
  initVoice(status);
  $("banner").textContent = problems.join(" / ");
  $("banner").hidden = !problems.length;
  await refreshChats();
  await refreshUsage();
}

function showAuth(setup) {
  $("app").hidden = true;
  $("auth").hidden = false;
  $("auth").dataset.mode = setup ? "setup" : "login";
  $("auth-hint").textContent = setup ? "最初のアカウントを作ります" : "ログインしてください";
  $("auth-submit").textContent = setup ? "作成" : "ログイン";
}

$("auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("auth-error").textContent = "";
  try {
    await post(`auth/${$("auth").dataset.mode}`, {username: $("username").value, password: $("password").value});
    await boot();
  } catch (error) {
    $("auth-error").textContent = error.message;
  }
});

$("logout").addEventListener("click", async () => {
  await post("auth/logout");
  closeStream();
  await boot();
});

// ---- 会話 -------------------------------------------------------------------------------

async function refreshChats() {
  const chats = await api("conversations");
  $("chats").replaceChildren(...chats.map((chat) => {
    const row = Object.assign(document.createElement("div"), {className: "chat-row"});
    const button = Object.assign(document.createElement("button"), {type: "button", textContent: chat.title});
    button.setAttribute("aria-current", String(chat.id === state.conversation));
    button.addEventListener("click", () => openChat(chat.id));
    const remove = Object.assign(document.createElement("button"), {type: "button", className: "quiet remove", textContent: "✕"});
    remove.setAttribute("aria-label", `「${chat.title}」を消す`);
    remove.addEventListener("click", () => deleteChat(chat));
    row.append(button, remove);
    return row;
  }));
  const current = chats.find((chat) => chat.id === state.conversation);
  if (current) $("title").textContent = current.title;
  if (state.conversation === null && chats.length) await openChat(chats[0].id);
}

// 会話を消す。資料も消すかはダイアログで選ぶ
async function deleteChat(chat) {
  const dialog = $("delete-dialog");
  $("delete-title").textContent = chat.title;
  $("delete-materials").checked = false;
  dialog.showModal();
  const answer = await new Promise((resolve) => dialog.addEventListener("close", () => resolve(dialog.returnValue), {once: true}));
  if (answer !== "delete") return;
  try {
    await removeChat(chat.id, $("delete-materials").checked);
  } catch (error) {
    alert(error.message);
  }
}

// 会話を消す (画面のダイアログと、空間の会話のパネルから)。失敗は投げる
async function removeChat(id, materialsToo = false) {
  await api(`conversations/${id}?materials=${materialsToo}`, {method: "DELETE"});
  if (state.conversation === id) {
    closeStream();
    state.conversation = null;
    $("log").replaceChildren();
    $("title").textContent = "MatDev";
    showWork(null);
  }
  await refreshChats();
  refreshPanel().catch(() => {});
}

// アシスタントの呼び名
function showName(name) {
  state.name = name || "ジャービス";
  $("assistant-name").value = state.name;
  $("assistant-label").textContent = `アシスタント: ${state.name}`;
  $("input").placeholder = `${state.name}への依頼`;
}

$("assistant-name").addEventListener("change", async () => {
  try {
    const {assistant_name} = await api("preferences", {method: "PUT", body: JSON.stringify({assistant_name: $("assistant-name").value})});
    showName(assistant_name);
  } catch (error) {
    alert(error.message);
    showName(state.name);
  }
});

async function refreshUsage() {
  const usage = await api("usage");
  const limit = usage.limit_usd === null ? "" : ` / ${usage.limit_usd}`;
  $("usage").textContent = `今月の目安 ${usage.cost_usd.toFixed(2)}${limit} USD`;
  const share = usage.limit_usd ? Math.min(1, usage.cost_usd / usage.limit_usd) : 0;
  $("usage-bar").style.width = `${(share * 100).toFixed(1)}%`;
  $("usage-bar").classList.toggle("high", share >= 0.8);
}

// 頭脳 (LLM) とつないだアプリの状態
function renderSystems(status) {
  const rows = [[`頭脳: ${status.llm}`, status.llm_ready, status.llm_ready ? "" : "鍵がありません"]];
  for (const app of status.apps || []) rows.push([`${app.name}: 道具 ${app.tools}`, !app.error, app.error || ""]);
  $("systems").replaceChildren(...rows.map(([label, ok, problem]) => {
    const row = Object.assign(document.createElement("li"), {className: ok ? "ok" : "bad", textContent: label});
    if (problem) row.title = problem;
    return row;
  }));
}

async function openChat(id) {
  state.conversation = id;
  toggleSide(false);
  closeStream();
  await reload();
  openStream();
  await refreshChats();
  refreshPanel().catch(() => {});
}

async function reload() {
  const data = await api(`conversations/${state.conversation}`);
  $("title").textContent = data.title;
  showWork(data.work);
  if (!data.work) {   // 出力先のない (前からある) 会話: 決めてもらう
    const work = await pickWork({title: "この会話の出力先 (作業) を決める",
                                 note: "計算の結果と構造を置くディレクトリです。決めるまで依頼は送れません"});
    if (work) {
      await api(`conversations/${state.conversation}/work`, {method: "PUT", body: JSON.stringify({work_id: work.id})});
      showWork(work);
    }
  }
  state.draft = null;
  state.draftText = "";
  $("log").replaceChildren();
  for (const item of data.items) renderItem(item, data.pending);
  setBusy(data.busy);
  scrollDown();
}

function closeStream() {
  if (state.source) state.source.close();
  state.source = null;
}

function openStream() {
  const source = new EventSource(`/api/conversations/${state.conversation}/events`);
  state.source = source;
  source.onmessage = (message) => onEvent(JSON.parse(message.data));
  source.onerror = () => {
    // 切れている間の動きは履歴にあるので、読み直してつなぎ直す
    if (source.readyState !== EventSource.CLOSED) return;
    setTimeout(() => {
      if (state.source === source) reload().then(openStream).catch(() => {});
    }, 2000);
  };
}

function onEvent(event) {
  if (event.event === "delta") {
    appendDelta(event.text);
  } else if (event.event === "item") {
    if (event.item.type === "assistant") clearDraft();
    renderItem(event.item, [event.item.data.confirmation_id]);
    speakItem(event.item);
    if (event.item.type === "tool") schedulePanel();
  } else if (event.event === "delta_end") {
    if (!state.draftText) clearDraft();
  } else if (event.event === "state") {
    setBusy(event.busy);
    if (!event.busy) {
      clearDraft();
      refreshUsage().catch(() => {});
      refreshChats().catch(() => {});
      refreshPanel().catch(() => {});
    }
  }
  if (event.event !== "delta") window.spaceRefresh?.();
  if (event.event === "confirmed") {
    for (const card of document.querySelectorAll(`[data-confirmation="${event.confirmation_id}"]`)) closeCard(card, event.status);
    confirmationClosed(event.confirmation_id);
  }
  scrollDown();
}

function appendDelta(text) {
  if (!state.draft) {
    state.draft = Object.assign(document.createElement("div"), {className: "msg assistant"});
    $("log").append(state.draft);
  }
  state.draftText += text;
  renderMarkdown(state.draft, state.draftText);
}

function clearDraft() {
  if (state.draft) state.draft.remove();
  state.draft = null;
  state.draftText = "";
}

function renderItem(item, pending) {
  const {type, data} = item;
  let node;
  if (type === "user") {
    node = Object.assign(document.createElement("div"), {className: `msg user${data.voice ? " voice" : ""}`, textContent: data.text});
  } else if (type === "assistant") {
    node = Object.assign(document.createElement("div"), {className: "msg assistant"});
    renderMarkdown(node, data.text);
  } else if (type === "tool") {
    const label = data.ok ? `✓ ${data.name}` : `✗ ${data.name}: ${data.error}`;
    node = Object.assign(document.createElement("div"), {className: `step${data.ok ? "" : " bad"}`, textContent: label});
  } else if (type === "notice") {
    node = Object.assign(document.createElement("div"), {className: `notice ${data.level}`, textContent: data.text});
  } else if (type === "action") {   // 画面 (作業台・道具) でやったこと。次の依頼でジャービスに伝える
    node = Object.assign(document.createElement("div"), {className: `step action${data.ok === false ? " bad" : ""}`});
    if (data.result) {   // 道具の結果は畳んで出す
      const details = document.createElement("details");
      details.append(Object.assign(document.createElement("summary"), {textContent: `${data.text}${data.ok === false ? " (失敗)" : ""}`}),
                     Object.assign(document.createElement("pre"), {className: "file-text", textContent: prettyResult(data.result)}));
      node.append(details);
    } else {
      node.textContent = data.text;
    }
  } else if (type === "confirm") {
    node = confirmCard(data, pending.includes(data.confirmation_id));
  } else if (type === "job") {   // 終わった計算の結果 (グラフ・図・構造の時間変化) を会話の中で開く
    node = jobCard(data, item.created_at);
  } else if (type === "structure") {
    node = Object.assign(document.createElement("div"), {className: "structure-card"});
    if (window.mountStructure) {
      window.mountStructure(node, data.app, data.structure_id);
    } else {   // structure3d.js (モジュール) を読み込んだあとに作る
      Object.assign(node.dataset, {pending: "1", app: data.app, structure: data.structure_id});
    }
  } else {
    return;
  }
  const anchor = state.draft;
  if (anchor) anchor.before(node); else $("log").append(node);
}

function jobCard(data, createdAt) {
  const card = Object.assign(document.createElement("div"), {className: `job-card${data.status === "done" ? "" : " bad"}`});
  const labels = {done: "完了", failed: "失敗", cancelled: "中止"};
  const head = Object.assign(document.createElement("div"), {className: "card-head"});
  head.append(Object.assign(document.createElement("b"), {textContent: `計算の結果: ${data.app} のジョブ ${data.job_id}`}),
              Object.assign(document.createElement("small"), {textContent: labels[data.status] || data.status}));
  const viewer = Object.assign(document.createElement("div"), {className: "job-viewer"});
  const toggle = Object.assign(document.createElement("button"), {type: "button", className: "small"});
  let shown = false;
  const open = () => {
    shown = !shown;
    toggle.textContent = shown ? "結果を閉じる" : "結果を開く";
    viewer.replaceChildren();
    if (shown) showJob(viewer, data.app, data.job_id);
  };
  toggle.addEventListener("click", open);
  card.append(head);
  if (data.message && data.status !== "done") card.append(Object.assign(document.createElement("p"), {className: "meta", textContent: data.message}));
  card.append(toggle, viewer);
  // 1 つだけの投入は開いておく (古い会話を開いたときは閉じておき、グラフをまとめて読みに行かない)
  if (data.open && Date.now() - Date.parse(createdAt || 0) < 10 * 60 * 1000) open(); else toggle.textContent = "結果を開く";
  return card;
}

function confirmCard(data, active) {
  const card = Object.assign(document.createElement("div"), {className: "confirm"});
  card.dataset.confirmation = data.confirmation_id;
  card.append(Object.assign(document.createElement("b"), {textContent: "実行してよいですか"}),
              Object.assign(document.createElement("p"), {textContent: data.summary}));
  if (active) {
    confirmationOpened(data);
    const buttons = document.createElement("div");
    for (const [label, approve, style] of [["実行する", true, "primary"], ["やめる", false, "quiet"]]) {
      const button = Object.assign(document.createElement("button"), {type: "button", textContent: label, className: style});
      button.addEventListener("click", async () => {
        try {
          await post(`confirmations/${data.confirmation_id}`, {approve});
        } catch (error) {
          closeCard(card, "expired");
        }
      });
      buttons.append(button);
    }
    card.append(buttons);
  } else {
    closeCard(card, "expired");
  }
  return card;
}

function closeCard(card, status) {
  confirmationClosed(card.dataset.confirmation);
  const labels = {approved: "実行しました", denied: "やめました", expired: "この確認は終わっています"};
  card.querySelector("div")?.remove();
  card.querySelector("b").textContent = labels[status] || status;
}

function setBusy(busy) {
  state.busy = busy;
  $("busy").hidden = !busy;
  $("reactor").classList.toggle("active", busy);
  $("stop").hidden = !busy;
  $("send").hidden = busy;
}

function scrollDown() {
  const log = $("log");
  log.scrollTop = log.scrollHeight;
}

// 新しい会話: 先に計算の結果の出力先 (作業) を決める
async function newChat() {
  const work = await pickWork({title: "新しい会話の出力先 (作業)",
                               note: "この会話の計算の結果と構造を置くディレクトリを決めてください"});
  if (!work) return null;
  const {id} = await post("conversations", {work_id: work.id});
  await openChat(id);
  return id;
}

$("new-chat").addEventListener("click", () => newChat().catch((error) => alert(error.message)));

async function sendMessage(text, spoken = false) {
  if (state.conversation === null && !(await newChat())) return;
  await post(`conversations/${state.conversation}/messages`, {text, voice: spoken, viewing: viewingNow()});
}

$("composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = $("input").value.trim();
  if (!text) return;
  $("input").value = "";
  resizeInput();
  voice.lastWasVoice = false;
  await sendMessage(text);
});

$("input").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) $("composer").requestSubmit();
});

function resizeInput() {
  const input = $("input");
  input.style.height = "auto";
  input.style.height = `${input.scrollHeight + 2}px`;
}

$("input").addEventListener("input", resizeInput);
$("stop").addEventListener("click", () => post(`conversations/${state.conversation}/stop`));

// ---- 画面の部品 -------------------------------------------------------------------------

// 狭い画面では、会話の一覧と資料を横から引き出す
function toggleSide(open) {
  $("side").classList.toggle("open", open);
  if (open) $("panel").classList.remove("open");
  $("scrim").hidden = !open;
}

function togglePanel(open) {
  $("panel").classList.toggle("open", open);
  if (open) {
    $("side").classList.remove("open");
    refreshPanel().catch(() => {});
  }
  $("scrim").hidden = !open;
}

$("theme").value = savedTheme();
$("theme").addEventListener("change", () => chooseTheme($("theme").value));

$("menu").addEventListener("click", () => toggleSide(true));
$("open-panel").addEventListener("click", () => togglePanel(true));
$("close-panel").addEventListener("click", () => togglePanel(false));
$("scrim").addEventListener("click", () => { toggleSide(false); togglePanel(false); });

boot();
