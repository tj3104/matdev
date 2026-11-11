"use strict";

// 表示の色: dark (既定) / light / auto (端末の設定に合わせる)。この端末のブラウザにだけ覚える
const THEME_KEY = "matdev.theme";
const THEME_COLORS = {dark: "#04080c", light: "#eef4f7"};

function savedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return ["dark", "light", "auto"].includes(value) ? value : "dark";
  } catch {
    return "dark";
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const light = theme === "light" || (theme === "auto" && matchMedia("(prefers-color-scheme: light)").matches);
  for (const meta of document.querySelectorAll('meta[name="theme-color"]')) meta.content = THEME_COLORS[light ? "light" : "dark"];
}

function chooseTheme(theme) {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // 覚えられなくても、この表示のあいだは切り替える
  }
  applyTheme(theme);
}

applyTheme(savedTheme());   // 画面を描く前に決めて、ちらつきを防ぐ
matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => applyTheme(savedTheme()));
