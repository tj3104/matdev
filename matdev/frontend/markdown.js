// ジャービスの返事 (Markdown) を DOM にする。HTML は解釈せず、すべて文字として入れる。
"use strict";

function inlineMarkdown(parent, text) {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*)/g;
  let last = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(text.slice(last, match.index));
    const token = match[0];
    const element = document.createElement(token[0] === "`" ? "code" : "strong");
    element.textContent = token[0] === "`" ? token.slice(1, -1) : token.slice(2, -2);
    parent.append(element);
    last = match.index + token.length;
  }
  parent.append(text.slice(last));
}

function tableRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function renderMarkdown(parent, source) {
  parent.replaceChildren();
  const lines = source.split("\n");
  let index = 0;
  const add = (tag, text) => {
    const element = document.createElement(tag);
    inlineMarkdown(element, text);
    parent.append(element);
  };
  while (index < lines.length) {
    const line = lines[index];
    if (line.startsWith("```")) {
      const code = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]);
      index += 1;
      const pre = document.createElement("pre");
      pre.append(Object.assign(document.createElement("code"), {textContent: code.join("\n")}));
      parent.append(pre);
    } else if (/^#{1,4} /.test(line)) {
      const level = line.match(/^#+/)[0].length;
      add(`h${Math.min(level + 2, 6)}`, line.replace(/^#+ /, ""));
      index += 1;
    } else if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|[\s:|-]+\|\s*$/.test(lines[index + 1] || "")) {
      const table = document.createElement("table");
      const head = table.createTHead().insertRow();
      for (const cell of tableRow(line)) {
        const th = document.createElement("th");
        inlineMarkdown(th, cell);
        head.append(th);
      }
      const body = table.createTBody();
      index += 2;
      while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
        const row = body.insertRow();
        for (const cell of tableRow(lines[index++])) inlineMarkdown(row.insertCell(), cell);
      }
      const wrap = document.createElement("div");
      wrap.className = "table";
      wrap.append(table);
      parent.append(wrap);
    } else if (/^\s*([-*]|\d+\.) /.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const list = document.createElement(ordered ? "ol" : "ul");
      while (index < lines.length && /^\s*([-*]|\d+\.) /.test(lines[index])) {
        const item = document.createElement("li");
        inlineMarkdown(item, lines[index++].replace(/^\s*([-*]|\d+\.) /, ""));
        list.append(item);
      }
      parent.append(list);
    } else if (line.trim() === "") {
      index += 1;
    } else {
      const paragraph = [];
      while (index < lines.length && lines[index].trim() !== "" && !/^(```|#{1,4} |\s*\|.*\|\s*$|\s*([-*]|\d+\.) )/.test(lines[index])) {
        paragraph.push(lines[index++]);
      }
      if (!paragraph.length) paragraph.push(lines[index++]);
      add("p", paragraph.join("\n"));
    }
  }
}
