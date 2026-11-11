"use strict";

// ---- 声 ---------------------------------------------------------------------------------
// 耳: マイクで録音し、サーバー (この PC の Whisper) で文字にして依頼として送る。話し終わりは無音で判断する
// 口: ジャービスの返事・計算の終わり・実行の確認を、端末の読み上げで話す
// 確認のカードが出ているときの声は、依頼ではなく確認の答え (「実行」「やめる」) として扱う

const VOICE_KEY = "matdev.speak";
const voice = {
  speak: false, listening: false, busy: false, recorder: null, stream: null, context: null, timer: null,
  pending: new Map(),    // 答えを待っている確認 (番号 → 要約)
  lastWasVoice: false,   // 直前の依頼が声なら、確認を読み上げたあと続けて答えを聞く
};

const SILENCE_MS = 1200, MAX_MS = 30000, NOTHING_MS = 8000, LEVEL = 0.025;

function micSupported() {
  return Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
}

function setMicState(mode) {
  const mic = document.getElementById("mic");
  mic.dataset.state = mode;
  mic.setAttribute("aria-pressed", String(mode === "listening"));
  mic.textContent = {listening: "■ 聞いています", working: "変換中…", idle: "🎙 声"}[mode];
  mic.disabled = mode === "working";
  const spaceMic = document.getElementById("space-mic");
  spaceMic.textContent = mic.textContent;
  spaceMic.dataset.state = mode;
  spaceMic.disabled = mic.disabled;
  window.spaceRedraw?.();
}

async function toggleListening() {
  if (voice.listening) {
    stopListening();
    return;
  }
  unlockSpeech();
  speechSynthesis.cancel();   // 話している途中でも、押したら聞く側に切り替える
  try {
    voice.stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}});
  } catch (error) {
    showVoiceError("マイクを使えません。ブラウザでマイクを許可してください");
    return;
  }
  const type = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
  const recorder = new MediaRecorder(voice.stream, type ? {mimeType: type} : undefined);
  const chunks = [];
  let heard = false;
  recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
  recorder.onstop = () => {
    cleanupRecording();
    if (!heard || !chunks.length) {
      setMicState("idle");
      return;
    }
    transcribe(new Blob(chunks, {type: recorder.mimeType || type}));
  };
  voice.recorder = recorder;
  voice.listening = true;
  setMicState("listening");
  recorder.start();

  // 話し終わりを音の大きさで判断する
  voice.context = new AudioContext();
  const analyser = voice.context.createAnalyser();
  analyser.fftSize = 1024;
  voice.context.createMediaStreamSource(voice.stream).connect(analyser);
  const samples = new Float32Array(analyser.fftSize);
  const started = performance.now();
  let lastSound = started;
  voice.timer = setInterval(() => {
    analyser.getFloatTimeDomainData(samples);
    const level = Math.sqrt(samples.reduce((sum, v) => sum + v * v, 0) / samples.length);
    const now = performance.now();
    if (level > LEVEL) {
      heard = true;
      lastSound = now;
    }
    document.getElementById("mic").style.setProperty("--level", Math.min(1, level * 12).toFixed(2));
    if ((heard && now - lastSound > SILENCE_MS) || now - started > MAX_MS || (!heard && now - started > NOTHING_MS)) {
      stopListening();
    }
  }, 100);
}

function stopListening() {
  if (voice.recorder && voice.recorder.state !== "inactive") voice.recorder.stop();
  voice.listening = false;
}

function cleanupRecording() {
  clearInterval(voice.timer);
  voice.stream?.getTracks().forEach((track) => track.stop());
  voice.context?.close().catch(() => {});
  voice.stream = voice.context = voice.recorder = null;
  voice.listening = false;
}

async function transcribe(blob) {
  setMicState("working");
  const form = new FormData();
  form.append("audio", blob, blob.type.includes("mp4") ? "voice.mp4" : "voice.webm");
  try {
    const {text} = await api("speech/transcribe", {method: "POST", body: form});
    if (text) await heardText(text);
    else speak("聞き取れませんでした");
  } catch (error) {
    showVoiceError(error.message);
  } finally {
    setMicState("idle");
  }
}

// 確認を待っていれば答えとして、なければ依頼として扱う
async function heardText(text) {
  const [confirmationId, summary] = [...voice.pending.entries()].at(-1) || [];
  if (confirmationId !== undefined) {
    const plain = text.replace(/[\s、。！？!?]/g, "");
    const deny = /やめ|中止|キャンセル|いいえ|だめ|ダメ|ストップ|しない/.test(plain);
    const approve = !deny && /^(はい、?)?(実行|じっこう|お願い|おねがい|進めて|オーケー|OK)/i.test(plain);
    if (!deny && !approve) {
      speak("「実行」か「やめる」で答えてください", () => maybeListenAgain());
      return;
    }
    await post(`confirmations/${confirmationId}`, {approve}).catch(() => {});
    voice.pending.delete(confirmationId);
    speak(approve ? "実行します" : "やめました");
    return;
  }
  voice.lastWasVoice = true;
  await sendMessage(text, true);
}

function maybeListenAgain() {
  if (voice.lastWasVoice && voice.pending.size && !voice.listening) toggleListening();
}

function showVoiceError(text) {
  const note = Object.assign(document.createElement("div"), {className: "notice error", textContent: text});
  $("log").append(note);
  scrollDown();
}

// ---- 口 ---------------------------------------------------------------------------------

// 読み上げの辞書: 単位・記号・元素を読み方に直す
const READINGS = [
  [/\[([^\]]+)\]\([^)]+\)/g, "$1"],
  [/https?:\/\/\S+/g, ""],
  [/(\d)\s*meV\b/g, "$1ミリ電子ボルト"], [/(\d)\s*eV\b/g, "$1電子ボルト"], [/(\d)\s*K\b/g, "$1ケルビン"],
  [/(\d)\s*GPa\b/g, "$1ギガパスカル"], [/(\d)\s*ps\b/g, "$1ピコ秒"], [/(\d)\s*ns\b/g, "$1ナノ秒"], [/(\d)\s*fs\b/g, "$1フェムト秒"],
  [/Å\^?3|Å³/g, "立方オングストローム"], [/Å/g, "オングストローム"], [/(\d)\s*USD\b/g, "$1ドル"],
  [/\bhcp\b/g, "エイチシーピー"], [/\bbcc\b/g, "ビーシーシー"], [/\bfcc\b/g, "エフシーシー"],
  [/\bGPUMD\b/g, "ジーピーユーエムディー"], [/\bNEP/g, "ネップ"], [/\bMD\b/g, "エムディー"], [/\bMatSim\b/gi, "マットシム"],
  [/\bTi(?![a-z])/g, "チタン"], [/\bCu(?![a-z])/g, "銅"], [/\bNi(?![a-z])/g, "ニッケル"], [/\bAg(?![a-z])/g, "銀"],
  [/\bAu(?![a-z])/g, "金"], [/\bSi(?![a-z])/g, "シリコン"], [/\bGe(?![a-z])/g, "ゲルマニウム"], [/\bAl(?![a-z])/g, "アルミニウム"],
  [/\bFe(?![a-z])/g, "鉄"], [/→/g, "から"], [/~|〜/g, "から"], [/±/g, "プラスマイナス"],
];
const SPOKEN_LIMIT = 500;

function spokenText(markdown) {
  let tableNoted = false, inCode = false;
  const lines = [];
  for (const raw of markdown.split("\n")) {
    const line = raw.trim();
    if (line.startsWith("```")) {   // コードは読まない
      if (!inCode) lines.push("コードは画面に出しました。");
      inCode = !inCode;
      continue;
    }
    if (inCode) continue;
    if (line.startsWith("|")) {   // 表は読まない (画面で見る)
      if (!tableNoted) lines.push("表は画面に出しました。");
      tableNoted = true;
      continue;
    }
    lines.push(line.replace(/^#+\s*|^[-*+]\s+|^\d+\.\s+|^>\s*/, "").replace(/[*_`#]/g, ""));
  }
  let text = lines.filter(Boolean).join("。").replace(/。{2,}/g, "。").replace(/([。！？])。/g, "$1");
  for (const [pattern, reading] of READINGS) text = text.replace(pattern, reading);
  if (text.length > SPOKEN_LIMIT) text = text.slice(0, SPOKEN_LIMIT) + "。続きは画面をご覧ください。";
  return text;
}

function japaneseVoice() {
  const voices = speechSynthesis.getVoices().filter((v) => v.lang.replace("_", "-").startsWith("ja"));
  return voices.find((v) => /Google|Kyoko|O-ren|Nanami|Haruka/.test(v.name)) || voices[0] || null;
}

function speak(text, done) {
  if (!voice.speak || !("speechSynthesis" in window) || !text) {
    done?.();
    return;
  }
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ja-JP";
  utterance.rate = 1.1;
  const chosen = japaneseVoice();
  if (chosen) utterance.voice = chosen;
  utterance.onend = () => done?.();
  speechSynthesis.speak(utterance);
}

// iPhone などは、利用者の操作の中で一度話さないと読み上げが鳴らない
function unlockSpeech() {
  if (voice.unlocked || !("speechSynthesis" in window)) return;
  voice.unlocked = true;
  speechSynthesis.speak(new SpeechSynthesisUtterance(""));
}

// 会話に届いたもの (履歴の読み直しは除く) を読み上げる
function speakItem(item) {
  const {type, data} = item;
  if (type === "assistant") {
    speak(spokenText(data.text));
  } else if (type === "notice" && data.level !== "info") {
    speak(data.text);
  } else if (type === "notice" && data.text.startsWith("計算が終わりました")) {
    speak("計算が終わりました");
  } else if (type === "confirm") {
    speak(`確認です。${spokenText(data.summary)}。実行してよいですか。「実行」か「やめる」で答えてください`, () => maybeListenAgain());
  }
}

function confirmationOpened(data) {
  voice.pending.set(data.confirmation_id, data.summary);
}

function confirmationClosed(id) {
  voice.pending.delete(Number(id));
}

function setSpeak(on) {
  voice.speak = on;
  const button = document.getElementById("speak-toggle");
  button.setAttribute("aria-pressed", String(on));
  button.querySelector(".icon").textContent = on ? "🔊" : "🔈";
  button.querySelector(".label").textContent = on ? "読み上げ" : "読み上げなし";
  button.setAttribute("aria-label", on ? "読み上げを止める" : "読み上げる");
  try {
    localStorage.setItem(VOICE_KEY, on ? "on" : "off");
  } catch {
    // 覚えられなくても、この表示のあいだは切り替える
  }
  if (!on) speechSynthesis?.cancel();
}

function initVoice(status) {
  const mic = document.getElementById("mic");
  const ready = status.speech?.ready && micSupported();
  mic.hidden = !ready;
  if (status.speech && !status.speech.ready) mic.title = status.speech.error || "音声認識は使えません";
  let saved = "off";
  try {
    saved = localStorage.getItem(VOICE_KEY) || "off";
  } catch {
    saved = "off";
  }
  document.getElementById("speak-toggle").hidden = !("speechSynthesis" in window);
  setSpeak(saved === "on");
  setMicState("idle");
}

// app.js より先に読むので、ここでは $ を使わない
document.getElementById("mic").addEventListener("click", () => toggleListening());
document.getElementById("speak-toggle").addEventListener("click", () => {
  unlockSpeech();
  setSpeak(!voice.speak);
});
