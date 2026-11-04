// Formatting for a body the gateway kept a prefix of.

/**
 * JSON, one item per line — even when the body was cut.
 *
 * The stored preview is a prefix, so it is usually *not* parseable JSON, and
 * `JSON.parse` refuses exactly the long responses worth reading. This walks the
 * text instead and only tracks whether it is inside a string, so a truncated
 * body still gets its shape back and a body that is not JSON at all comes out
 * unchanged.
 */
export function indentJson(text) {
  const source = String(text ?? "");
  // No structure to shape: a plain sentence, and squeezing its spaces would be
  // the one thing this function must not do. SSE frames (`data: {...}`) still
  // have braces and go through the walk.
  if (!/[{[]/.test(source)) return source.trim();
  const pad = (depth) => "  ".repeat(depth);
  let out = "";
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (const char of source) {
    if (inString) {
      out += char;
      if (escaped) escaped = false;
      else if (char === "\\") escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') {
      inString = true;
      out += char;
    } else if (char === "{" || char === "[") {
      depth += 1;
      out += `${char}\n${pad(depth)}`;
    } else if (char === "}" || char === "]") {
      depth = Math.max(0, depth - 1);
      out += `\n${pad(depth)}${char}`;
    } else if (char === ",") {
      out += `,\n${pad(depth)}`;
    } else if (char === ":") {
      out += ": ";
    } else if (char !== " " && char !== "\n" && char !== "\t") {
      out += char;
    }
  }
  return out.trim();
}

/** Handing the browser a file without a server round trip. */
export function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  // Revoking in the same tick cancels the download in Safari.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
