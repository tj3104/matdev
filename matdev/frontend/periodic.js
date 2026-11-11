"use strict";

// ---- 周期表 -------------------------------------------------------------------------------------
// 元素を選ぶ欄で使う。普段の画面はポップアップ (pickElement)、空間の作業台はパネルに描く (space.js が PERIODIC を使う)。
// 位置は 18 族の表の (行, 列)。ランタノイド・アクチノイドは下の 2 行 (8・9 行目) に分けて置く。

const PERIODIC = (() => {
  const rows = [
    [1, "H", 1], [1, "He", 18],
    [2, "Li", 1], [2, "Be", 2], ...["B", "C", "N", "O", "F", "Ne"].map((s, i) => [2, s, 13 + i]),
    [3, "Na", 1], [3, "Mg", 2], ...["Al", "Si", "P", "S", "Cl", "Ar"].map((s, i) => [3, s, 13 + i]),
    ...["K", "Ca", "Sc", "Ti", "V", "Cr", "Mn", "Fe", "Co", "Ni", "Cu", "Zn", "Ga", "Ge", "As", "Se", "Br", "Kr"].map((s, i) => [4, s, 1 + i]),
    ...["Rb", "Sr", "Y", "Zr", "Nb", "Mo", "Tc", "Ru", "Rh", "Pd", "Ag", "Cd", "In", "Sn", "Sb", "Te", "I", "Xe"].map((s, i) => [5, s, 1 + i]),
    [6, "Cs", 1], [6, "Ba", 2], [6, "La", 3],
    ...["Hf", "Ta", "W", "Re", "Os", "Ir", "Pt", "Au", "Hg", "Tl", "Pb", "Bi", "Po", "At", "Rn"].map((s, i) => [6, s, 4 + i]),
    [7, "Fr", 1], [7, "Ra", 2], [7, "Ac", 3],
    ...["Rf", "Db", "Sg", "Bh", "Hs", "Mt", "Ds", "Rg", "Cn", "Nh", "Fl", "Mc", "Lv", "Ts", "Og"].map((s, i) => [7, s, 4 + i]),
    ...["Ce", "Pr", "Nd", "Pm", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu"].map((s, i) => [8, s, 4 + i]),
    ...["Th", "Pa", "U", "Np", "Pu", "Am", "Cm", "Bk", "Cf", "Es", "Fm", "Md", "No", "Lr"].map((s, i) => [9, s, 4 + i]),
  ];
  return rows.map(([row, symbol, col]) => ({symbol, row, col}));
})();

// 元素の欄の選択肢: 構造の元素だけか、すべての元素か
function elementChoices(field, structureSymbols) {
  return field.source === "structure" ? structureSymbols : PERIODIC.map((e) => e.symbol);
}

// 普段の画面: 周期表のポップアップで 1 つ選ぶ。やめたら null
function pickElement({current, enabled, highlight = [], allowAll = false, title = "元素を選ぶ"}) {
  return new Promise((resolve) => {
    const dialog = Object.assign(document.createElement("dialog"), {className: "hud dialog periodic"});
    const head = Object.assign(document.createElement("div"), {className: "periodic-head"});
    head.append(Object.assign(document.createElement("b"), {textContent: title}));
    const close = Object.assign(document.createElement("button"), {type: "button", className: "quiet icon", textContent: "✕"});
    close.setAttribute("aria-label", "やめる");
    head.append(close);
    const grid = Object.assign(document.createElement("div"), {className: "periodic-grid"});
    let chosen = null;
    const finish = (value) => {
      chosen = value;
      dialog.close();
    };
    if (allowAll) {
      const all = Object.assign(document.createElement("button"), {type: "button", className: "periodic-all", textContent: "すべて"});
      all.setAttribute("aria-pressed", String(current === "*"));
      all.addEventListener("click", () => finish("*"));
      head.insertBefore(all, close);
    }
    for (const {symbol, row, col} of PERIODIC) {
      const cell = Object.assign(document.createElement("button"), {type: "button", textContent: symbol});
      cell.style.gridRow = String(row + (row >= 8 ? 1 : 0));   // f ブロックの前に 1 行あける
      cell.style.gridColumn = String(col);
      cell.disabled = enabled ? !enabled.includes(symbol) : false;
      if (highlight.includes(symbol)) cell.classList.add("in-structure");
      cell.setAttribute("aria-pressed", String(symbol === current));
      cell.addEventListener("click", () => finish(symbol));
      grid.append(cell);
    }
    const legend = Object.assign(document.createElement("p"), {className: "meta",
      textContent: enabled ? "構造にある元素から選びます" : "光っているのは構造にある元素です"});
    close.addEventListener("click", () => finish(null));
    dialog.append(head, grid, legend);
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(chosen);
    });
    document.body.append(dialog);
    dialog.showModal();
  });
}
