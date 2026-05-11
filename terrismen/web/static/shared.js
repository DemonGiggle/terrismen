export async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || `Request failed: ${response.status}`);
  }
  return payload;
}

export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function renderPlainText(value) {
  return escapeHtml(value).replace(/\n/g, "<br>");
}

let pendingMathTypeset = Promise.resolve();

export function renderMarkdown(value) {
  const lines = String(value || "").replace(/\r\n/g, "\n").split("\n");
  const blocks = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fenceMatch = line.match(/^```(\w+)?\s*$/);
    if (fenceMatch) {
      index += 1;
      const codeLines = [];
      while (index < lines.length && !lines[index].match(/^```\s*$/)) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      const language = fenceMatch[1] ? ` data-language="${escapeAttribute(fenceMatch[1])}"` : "";
      blocks.push(`<pre class="code-block"${language}><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      continue;
    }

    const displayMath = parseDisplayMathBlock(lines, index);
    if (displayMath) {
      blocks.push(displayMath.html);
      index = displayMath.nextIndex;
      continue;
    }

    const headingMatch = line.match(/^(#{1,3})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length + 2;
      blocks.push(`<h${level}>${renderInlineMarkdown(headingMatch[2].trim())}</h${level}>`);
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push(`<blockquote>${renderInlineMarkdown(quoteLines.join("\n")).replace(/\n/g, "<br>")}</blockquote>`);
      continue;
    }

    if (/^\s*[-*]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*]\s+/, ""));
        index += 1;
      }
      blocks.push(`<ul>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ul>`);
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (index < lines.length && /^\s*\d+[.)]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+[.)]\s+/, ""));
        index += 1;
      }
      blocks.push(`<ol>${items.map((item) => `<li>${renderInlineMarkdown(item)}</li>`).join("")}</ol>`);
      continue;
    }

    if (isMarkdownTableHeader(line, lines[index + 1])) {
      const headerCells = splitMarkdownTableRow(line);
      index += 2;
      const bodyRows = [];
      while (index < lines.length && isMarkdownTableRow(lines[index])) {
        bodyRows.push(splitMarkdownTableRow(lines[index]));
        index += 1;
      }
      blocks.push(renderMarkdownTable(headerCells, bodyRows));
      continue;
    }

    const paragraphLines = [];
    while (
      index < lines.length &&
      lines[index].trim() &&
      !lines[index].match(/^```(\w+)?\s*$/) &&
      !lines[index].match(/^(#{1,3})\s+(.+)$/) &&
      !/^>\s?/.test(lines[index]) &&
      !/^\s*[-*]\s+/.test(lines[index]) &&
      !/^\s*\d+[.)]\s+/.test(lines[index])
    ) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    blocks.push(`<p>${renderInlineMarkdown(paragraphLines.join("\n")).replace(/\n/g, "<br>")}</p>`);
  }

  return blocks.join("");
}

export function typesetMath(root) {
  if (!root || !window.MathJax?.typesetPromise) {
    return Promise.resolve();
  }

  pendingMathTypeset = pendingMathTypeset
    .then(async () => {
      if (window.MathJax.typesetClear) {
        window.MathJax.typesetClear([root]);
      }
      await window.MathJax.typesetPromise([root]);
    })
    .catch(() => {});

  return pendingMathTypeset;
}

export function getSettingsState(settings) {
  return isSettingsConfigured(settings)
    ? { isConfigured: true, label: "Configured", className: "tag tag-success is-hidden" }
    : { isConfigured: false, label: "Setup needed", className: "setup-notice" };
}

export function summarizeSettings(settings) {
  const missing = [];
  if (!settings.provider_type) {
    missing.push("provider");
  }
  if (!settings.base_url) {
    missing.push("base URL");
  }
  if (!settings.model) {
    missing.push("model");
  }
  if (missing.length) {
    return `Missing ${missing.join(", ")} before upload and chat are ready.`;
  }

  return [
    formatProviderType(settings.provider_type),
    settings.model,
    settings.base_url,
    settings.api_key ? "API key saved" : "No API key saved",
    `${Math.round(settings.llm_timeout_seconds ?? 600)}s timeout`,
  ].join(" • ");
}

function isSettingsConfigured(settings) {
  return Boolean(settings.provider_type && settings.base_url && settings.model);
}

function formatProviderType(providerType) {
  if (providerType === "openai_compatible") {
    return "OpenAI-compatible";
  }
  if (providerType === "ollama") {
    return "Ollama";
  }
  return "Unknown provider";
}

function renderInlineMarkdown(value) {
  const links = [];
  const codeSpans = [];
  const mathSpans = [];
  const codedValue = extractInlineCodeSpans(value, codeSpans);
  const mathValue = extractInlineMath(codedValue, mathSpans);
  let html = replaceMarkdownLinks(escapeHtml(mathValue), links, codeSpans, mathSpans);
  html = renderInlineStyles(html);
  html = restorePlaceholders(html, "CODE", codeSpans);
  html = restorePlaceholders(html, "MATH", mathSpans);
  return html.replace(/\u0000LINK(\d+)\u0000/g, (_, linkIndex) => links[Number(linkIndex)]);
}

function renderInlineStyles(value) {
  return value
    .replace(/\*\*([\s\S]+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([\s\S]+?)\*/g, "<em>$1</em>")
    .replace(/__([\s\S]+?)__/g, "<strong>$1</strong>")
    .replace(/_([\s\S]+?)_/g, "<em>$1</em>");
}

function replaceMarkdownLinks(value, links, codeSpans, mathSpans) {
  let result = "";
  let index = 0;

  while (index < value.length) {
    const linkStart = value.indexOf("[", index);
    if (linkStart === -1) {
      result += value.slice(index);
      break;
    }

    result += value.slice(index, linkStart);
    const parsedLink = parseMarkdownLink(value, linkStart);
    if (!parsedLink) {
      result += value[linkStart];
      index = linkStart + 1;
      continue;
    }

    const decodedUrl = decodeHtmlEntities(parsedLink.url);
    if (!isSafeUrl(decodedUrl)) {
      result += parsedLink.label;
      index = parsedLink.end;
      continue;
    }

    links.push(
      `<a href="${escapeAttribute(decodedUrl)}" target="_blank" rel="noopener noreferrer">${restorePlaceholders(restorePlaceholders(renderInlineStyles(parsedLink.label), "CODE", codeSpans), "MATH", mathSpans)}</a>`,
    );
    result += `\u0000LINK${links.length - 1}\u0000`;
    index = parsedLink.end;
  }

  return result;
}

function parseMarkdownLink(value, startIndex) {
  const labelEnd = value.indexOf("]", startIndex + 1);
  if (labelEnd === -1 || value[labelEnd + 1] !== "(") {
    return null;
  }

  let urlEnd = labelEnd + 2;
  let depth = 1;
  while (urlEnd < value.length) {
    const character = value[urlEnd];
    if (/\s/.test(character)) {
      return null;
    }
    if (character === "(") {
      depth += 1;
    } else if (character === ")") {
      depth -= 1;
      if (depth === 0) {
        break;
      }
    }
    urlEnd += 1;
  }

  if (depth !== 0) {
    return null;
  }

  return {
    label: value.slice(startIndex + 1, labelEnd),
    url: value.slice(labelEnd + 2, urlEnd),
    end: urlEnd + 1,
  };
}

function decodeHtmlEntities(value) {
  const textarea = document.createElement("textarea");
  textarea.innerHTML = value;
  return textarea.value;
}

function isSafeUrl(value) {
  try {
    const parsed = new URL(value, window.location.origin);
    return ["http:", "https:", "mailto:"].includes(parsed.protocol);
  } catch {
    return false;
  }
}

function escapeAttribute(value) {
  return escapeHtml(value).replaceAll("'", "&#39;");
}

function isMarkdownTableHeader(headerLine, separatorLine) {
  return isMarkdownTableRow(headerLine) && isMarkdownTableSeparator(separatorLine);
}

function isMarkdownTableRow(line) {
  return typeof line === "string" && line.includes("|");
}

function isMarkdownTableSeparator(line) {
  if (typeof line !== "string") {
    return false;
  }
  const cells = splitMarkdownTableRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function splitMarkdownTableRow(line) {
  return String(line)
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function renderMarkdownTable(headerCells, bodyRows) {
  const headerHtml = headerCells.map((cell) => `<th>${renderInlineMarkdown(cell)}</th>`).join("");
  const bodyHtml = bodyRows
    .map((row) => `<tr>${row.map((cell) => `<td>${renderInlineMarkdown(cell)}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="markdown-table-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

function parseDisplayMathBlock(lines, startIndex) {
  const line = lines[startIndex];
  const singleDollar = line.match(/^\s*\$\$(.+)\$\$\s*$/);
  if (singleDollar) {
    return { html: renderDisplayMath(singleDollar[1].trim()), nextIndex: startIndex + 1 };
  }

  const singleBracket = line.match(/^\s*\\\[(.+)\\\]\s*$/);
  if (singleBracket) {
    return { html: renderDisplayMath(singleBracket[1].trim()), nextIndex: startIndex + 1 };
  }

  if (/^\s*\$\$\s*$/.test(line)) {
    return parseMultiLineDisplayMath(lines, startIndex, /^\s*\$\$\s*$/);
  }

  if (/^\s*\\\[\s*$/.test(line)) {
    return parseMultiLineDisplayMath(lines, startIndex, /^\s*\\\]\s*$/);
  }

  return null;
}

function parseMultiLineDisplayMath(lines, startIndex, endPattern) {
  const contentLines = [];
  let index = startIndex + 1;

  while (index < lines.length && !endPattern.test(lines[index])) {
    contentLines.push(lines[index]);
    index += 1;
  }

  if (index >= lines.length) {
    return null;
  }

  return {
    html: renderDisplayMath(contentLines.join("\n").trim()),
    nextIndex: index + 1,
  };
}

function renderDisplayMath(value) {
  return `<div class="math-display">\\[${escapeHtml(value)}\\]</div>`;
}

function extractInlineCodeSpans(value, codeSpans) {
  return String(value).replace(/`([^`]+)`/g, (_, code) => {
    codeSpans.push(`<code>${escapeHtml(code)}</code>`);
    return `\u0000CODE${codeSpans.length - 1}\u0000`;
  });
}

function extractInlineMath(value, mathSpans) {
  let result = "";
  let index = 0;

  while (index < value.length) {
    if (value.startsWith("\\(", index) && !isEscapedAt(value, index)) {
      const endIndex = value.indexOf("\\)", index + 2);
      if (endIndex !== -1) {
        mathSpans.push(`<span class="math-inline">\\(${escapeHtml(value.slice(index + 2, endIndex))}\\)</span>`);
        result += `\u0000MATH${mathSpans.length - 1}\u0000`;
        index = endIndex + 2;
        continue;
      }
    }

    if (value.startsWith("$$", index) && !isEscapedAt(value, index)) {
      result += "$$";
      index += 2;
      continue;
    }

    if (value[index] === "$" && !isEscapedAt(value, index) && value[index + 1] !== "$") {
      const endIndex = findInlineDollarMathEnd(value, index + 1);
      if (endIndex !== -1) {
        mathSpans.push(`<span class="math-inline">\\(${escapeHtml(value.slice(index + 1, endIndex))}\\)</span>`);
        result += `\u0000MATH${mathSpans.length - 1}\u0000`;
        index = endIndex + 1;
        continue;
      }
    }

    result += value[index];
    index += 1;
  }

  return result;
}

function findInlineDollarMathEnd(value, startIndex) {
  for (let index = startIndex; index < value.length; index += 1) {
    if (value[index] === "\n") {
      return -1;
    }
    if (value.startsWith("$$", index) && !isEscapedAt(value, index)) {
      return -1;
    }
    if (value[index] === "$" && !isEscapedAt(value, index) && value[index + 1] !== "$") {
      return index;
    }
  }
  return -1;
}

function isEscapedAt(value, index) {
  let backslashCount = 0;
  for (let cursor = index - 1; cursor >= 0 && value[cursor] === "\\"; cursor -= 1) {
    backslashCount += 1;
  }
  return backslashCount % 2 === 1;
}

function restorePlaceholders(value, prefix, entries) {
  return value.replace(new RegExp(`\\u0000${prefix}(\\d+)\\u0000`, "g"), (_, entryIndex) => entries[Number(entryIndex)]);
}
