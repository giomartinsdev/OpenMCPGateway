// Formatting for a body the gateway kept a prefix of.

/**
 * JSON, one item per line — even when the body was cut.
 *
 * `JSON.parse` is not enough here twice over: the stored preview is a *prefix*,
 * so it usually is not valid JSON at all, and an MCP response over
 * streamable-HTTP is not JSON either — it is an SSE frame with the payload after
 * `data: `. So this walks the text and tracks only whether it is inside a
 * string: a truncated body still gets its shape back, a sentence that is not
 * JSON comes out untouched, and a frame keeps its envelope on its own line.
 */
function shape(text) {
  const pad = (depth) => "  ".repeat(depth);
  let out = "";
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
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
      // `{}` and `[]` are one token, not an opening a line above a closing.
      const closing = char === "{" ? "}" : "]";
      const rest = text.slice(index + 1).trimStart();
      if (rest.startsWith(closing)) {
        out += char + closing;
        index = text.length - rest.length;
      } else {
        depth += 1;
        out += `${char}\n${pad(depth)}`;
      }
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

export function indentJson(text) {
  const source = String(text ?? "").replace(/\r\n?/g, "\n").trim();
  // No structure to shape: a plain sentence, and squeezing its spaces would be
  // the one thing this function must not do.
  if (!/[{[]/.test(source)) return source;
  const lines = source.split("\n");
  // An unshaped body is one long line; an SSE body is `event: X` then `data: `.
  if (!lines.some((line) => line.startsWith("data: "))) return shape(source);
  return lines
    .map((line) => {
      const data = /^data: (.*)$/.exec(line);
      return data ? `data: ${shape(data[1])}` : line;
    })
    .join("\n");
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
