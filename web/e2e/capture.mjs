// Gera as capturas da landing e do README a partir de um deployment LOCAL.
//
// Não é um spec: mora fora de `e2e/specs/` de propósito, porque não é asserção
// de contrato, é material de divulgação. Roda contra um servidor que já está de
// pé (`bash e2e/serve.sh`), cria um tenant de demonstração com dados inventados
// e tira as telas.
//
//   node e2e/capture.mjs http://127.0.0.1:8123 ../site/img
//
// O tenant é novo a cada execução (sufixo aleatório), então rodar duas vezes num
// banco reaproveitado não colide em e-mail. Mas o *slug* do gateway é global, não
// por tenant: rode contra um `serve.sh` recém-subido.
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = process.argv[2] ?? "http://127.0.0.1:8123";
const OUT = process.argv[3] ?? "../site/img";
const suffix = Math.random().toString(36).slice(2, 7);
const OWNER = { email: `demo-${suffix}@example.test`, password: "demo-password-123" };
const TENANT = "Northwind Platform";

// O que a demo tem: o console (que responde de verdade), um gateway que só
// expõe uma tool (para o 403 da allowlist), um público com teto baixo (429) e um
// que fala OAuth. É a variedade que faz o painel mostrar 200, 401, 403 e 429 sem
// inventar linha.
const GATEWAYS = [
  { slug: "rancher", name: "Rancher", target_url: "http://127.0.0.1:9/mcp", auth_mode: "api_key", idp: null },
  { slug: "postgres", name: "Postgres read-only", target_url: "http://127.0.0.1:9/mcp", auth_mode: "api_key", idp: null },
  { slug: "github", name: "GitHub", target_url: "http://127.0.0.1:9/mcp", auth_mode: "oauth", idp: "github" },
  { slug: "public-demo", name: "Public demo", target_url: "http://127.0.0.1:9/mcp", auth_mode: "none", idp: null },
];

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const context = await browser.newContext({
  baseURL: BASE,
  viewport: { width: 1440, height: 900 },
  deviceScaleFactor: 2,
  colorScheme: "dark",
  locale: "en-US",
});
const page = await context.newPage();

async function shot(name, options = {}) {
  // Fonte pronta antes do clique do obturador: uma captura com fallback de
  // sistema é uma captura que não representa o produto.
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(options.settle ?? 450);
  await page.screenshot({ path: `${OUT}/${name}.png`, ...options });
  console.log(`  ${name}.png`);
}

// -- conta e tenant -------------------------------------------------------
await page.goto("/");
await page.getByRole("link", { name: /create an account/i }).click();
await page.getByLabel("Email", { exact: true }).fill(OWNER.email);
await page.getByLabel("Password", { exact: true }).fill(OWNER.password);
await page.getByRole("button", { name: /^create account$/i }).click();
await page.getByLabel("Tenant name").fill(TENANT);
await page.getByRole("button", { name: "Create tenant" }).click();
await page.getByRole("heading", { name: "Gateways", level: 1 }).waitFor();

// -- gateways -------------------------------------------------------------
for (const gateway of GATEWAYS) {
  await page.evaluate(async (body) => {
    const response = await fetch("/api/gateways", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(`${body.slug}: ${await response.text()}`);
  }, gateway);
}

// Um OAuth sem credenciais é o 503 do "fail closed", que é justamente o que a
// landing promete — vale ter a linha no log.
// -- tráfego real ---------------------------------------------------------
// Chamadas de verdade: o console gateway responde, e as outras rotas recusam.
// Sem isso a tela de tráfego é uma tabela vazia e a captura não prova nada.
//
// O transporte é stateful: `initialize` devolve `mcp-session-id` e as chamadas
// seguintes precisam dele, senão voltam `Missing session ID`. O `fetch` aqui não
// guarda cookie, então a sessão é carregada à mão.
const traffic = await page.evaluate(async () => {
  const listed = await fetch("/api/gateways", { credentials: "same-origin" });
  const gateways = (await listed.json()).gateways;
  const consoleGateway = gateways.find((row) => row.slug.startsWith("console"));

  const minted = await fetch(`/api/gateways/${consoleGateway.id}/keys`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: "demo" }),
  });
  const { api_key: key } = await minted.json();

  const call = (slug, headers, body) =>
    fetch(`/g/${slug}/mcp`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
        ...headers,
      },
      body: JSON.stringify(body),
    });

  // Uma resposta SSE é uma sequência de `data:` — o frame que interessa é o que
  // tem `result`.
  async function frame(response) {
    const text = await response.text();
    return (
      text
        .split(/\r?\n/)
        .filter((line) => line.startsWith("data: "))
        .map((line) => JSON.parse(line.slice(6)))
        .find((body) => body.result) ?? null
    );
  }

  const client = { name: "demo-agent", version: "1.0" };
  const handshake = await call(
    consoleGateway.slug,
    { Authorization: `Bearer ${key}` },
    {
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: client },
    },
  );
  const session = handshake.headers.get("mcp-session-id");
  const console = {
    Authorization: `Bearer ${key}`,
    "MCP-Protocol-Version": "2025-06-18",
    "mcp-session-id": session,
  };

  const list = await frame(
    await call(consoleGateway.slug, console, {
      jsonrpc: "2.0", id: 2, method: "tools/list", params: {},
    }),
  );
  const tools = list?.result?.tools ?? [];

  // As perguntas viram linhas com nome de tool, que é o que a coluna Tool mostra.
  const wanted = [
    "traffic_stats",
    "list_gateways",
    "search_requests",
    "search_events",
    "search_docs",
  ];
  for (const [index, name] of wanted.entries()) {
    if (!tools.some((tool) => tool.name === name)) continue;
    await call(consoleGateway.slug, console, {
      jsonrpc: "2.0", id: 10 + index, method: "tools/call",
      params: { name, arguments: name === "search_docs" ? { query: "rate limit" } : {} },
    });
  }

  // Recusas, uma por motivo — é a coluna Outcome que a tela explica.
  // 401: sem credencial nenhuma.
  const init = (id) => ({
    jsonrpc: "2.0", id, method: "initialize",
    params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: client },
  });
  for (const id of [30, 31, 32]) await call("rancher", {}, init(id));

  // 403: credencial válida, mas a tool pedida está fora da allowlist. É o gate
  // que a landing vende como "o gateway expõe um subconjunto", então ele precisa
  // de uma linha no log.
  const postgres = gateways.find((row) => row.slug === "postgres");
  await fetch(`/api/gateways/${postgres.id}/tools`, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tools: ["list_gateways"], source: "" }),
  });
  const postgresKey = await fetch(`/api/gateways/${postgres.id}/keys`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ label: "demo" }),
  }).then((response) => response.json());
  await call("postgres", { Authorization: `Bearer ${postgresKey.api_key}` }, {
    jsonrpc: "2.0", id: 34, method: "tools/call",
    params: { name: "search_docs", arguments: { query: "auth" } },
  });

  // 429: o limitador é por (gateway, subject), então um teto de 1 só recusa a
  // segunda chamada — o `initialize` tem `id`, logo conta como request JSON-RPC.
  const narrow = gateways.find((row) => row.slug === "public-demo");
  await fetch(`/api/gateways/${narrow.id}/rate-limit`, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ requests_per_minute: 1 }),
  });
  for (const id of [40, 41, 42]) await call("public-demo", {}, init(id));

  return { console: consoleGateway.slug, tools: tools.map((tool) => tool.name) };
});
console.log(`  console: ${traffic.console} · ${traffic.tools.length} tools`);

// Uma captura só vale se a tela tiver o que mostrar: falho aqui, antes do
// obturador, em vez de descobrir depois com um PNG de tabela vazia.
const seeded = await page.evaluate(async () => {
  const response = await fetch("/api/requests?limit=200", { credentials: "same-origin" });
  const rows = (await response.json()).requests;
  const byOutcome = {};
  for (const row of rows) byOutcome[row.outcome] = (byOutcome[row.outcome] ?? 0) + 1;
  return { total: rows.length, byOutcome, tools: new Set(rows.map((row) => row.tool).filter(Boolean)).size };
});
if (seeded.total < 8 || seeded.tools < 4 || !seeded.byOutcome.allowed || !seeded.byOutcome.forbidden) {
  throw new Error(`seed fraco: ${JSON.stringify({ ...seeded, byOutcome: seeded.byOutcome })}`);
}
console.log(`  traffic: ${seeded.total} rows · ${seeded.tools} tools · ${JSON.stringify(seeded.byOutcome)}`);

// -- capturas -------------------------------------------------------------
// Ordem: as telas públicas primeiro, porque são as que a landing usa.

await page.goto("/#/traffic");
await page.getByRole("heading", { name: "Traffic", level: 1 }).waitFor();
await page.waitForTimeout(1200); // o primeiro load dos stats
const rows = page.getByRole("row").filter({ has: page.getByRole("cell") });
if ((await rows.count()) < 8) throw new Error("traffic vazia na tela");
// A tabela pinta o outcome, e o texto vem do dicionário do painel ("401
// unauthenticated", não o código cru) — então a asserção usa o rótulo, não a
// chave.
for (const outcome of ["allowed", "401 unauthenticated", "403 forbidden", "429 rate limited"]) {
  if ((await page.getByText(outcome, { exact: true }).count()) === 0) {
    throw new Error(`a tela de tráfego não mostra "${outcome}"`);
  }
}
await shot("panel-traffic");

// Drawer de uma chamada que passou: é a que tem corpo dos dois lados para a
// formatação mostrar. Uma recusa tem request vazio, e provaria menos.
await rows.filter({ hasText: "allowed" }).first().click();
const drawer = page.getByRole("dialog");
await drawer.waitFor();
for (const block of await drawer.locator(".payload-block").all()) {
  await block.locator(".payload-body").waitFor();
}
await shot("panel-request-drawer");
await page.keyboard.press("Escape");

await page.goto("/#/");
await page.getByRole("heading", { name: "Gateways", level: 1 }).waitFor();
await page.waitForTimeout(1200);
await shot("panel-home");

await page.goto("/#/gateways");
await page.getByRole("heading", { name: "Gateways", level: 1 }).waitFor();
await shot("panel-gateways");

// A página do console: endpoint, tools com contagem, guia de conexão.
const consoleId = await page.evaluate(async () => {
  const listed = await fetch("/api/gateways", { credentials: "same-origin" });
  const gateways = (await listed.json()).gateways;
  return gateways.find((row) => row.slug.startsWith("console")).id;
});
await page.goto(`/#/gateways/${consoleId}`);
await page.getByRole("heading", { level: 1 }).first().waitFor();
await page.waitForTimeout(900);
await shot("panel-gateway-detail");

await page.goto("/#/audit");
await page.getByRole("heading", { name: "Audit log", level: 1 }).waitFor();
await shot("panel-audit");

await page.goto("/#/members");
await page.getByRole("heading", { name: "Members", level: 1 }).waitFor();
await shot("panel-members");

await page.goto("/#/docs");
await page.getByRole("heading", { level: 1 }).first().waitFor();
await shot("panel-docs");

// Tema claro: o mesmo painel, outra paleta. É a segunda captura do README.
await page.evaluate(() => localStorage.setItem("omg.theme", "light"));
await page.goto("/#/traffic");
await page.getByRole("heading", { name: "Traffic", level: 1 }).waitFor();
await page.waitForTimeout(1200);
await shot("panel-traffic-light");

// A landing do próprio produto, anônima: é a primeira imagem do README.
await page.evaluate(() => localStorage.removeItem("omg.theme"));
await context.clearCookies();
await page.goto("/");
await page.getByRole("heading", { level: 1 }).first().waitFor();
await shot("app-landing");

await browser.close();
console.log("ok");
