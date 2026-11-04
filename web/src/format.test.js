// Run with `npm run test:unit` (node's own runner; no extra dependency).
import assert from "node:assert/strict";
import test from "node:test";
import { indentJson } from "./format.js";

test("a JSON body comes back one item per line", () => {
  assert.equal(
    indentJson('{"a":1,"b":[2,{"c":"x,y"}]}'),
    '{\n  "a": 1,\n  "b": [\n    2,\n    {\n      "c": "x,y"\n    }\n  ]\n}',
  );
});

test("a truncated body still gets its shape back", () => {
  // The stored preview is a prefix, so this is the common case: `JSON.parse`
  // refuses it, the drawer still has to be readable.
  assert.equal(
    indentJson('{"jsonrpc":"2.0","result":{"tools":[{"name":"issu'),
    '{\n  "jsonrpc": "2.0",\n  "result": {\n    "tools": [\n      {\n        "name": "issu',
  );
});

test("an MCP response keeps its SSE envelope and shapes the frame", () => {
  // What the proxy actually stores for a streamable-HTTP call: the body is a
  // frame, not JSON, so shaping the whole thing would eat the `event:` line.
  assert.equal(
    indentJson('event: message\r\ndata: {"jsonrpc":"2.0","id":1,"result":{}}'),
    'event: message\ndata: {\n  "jsonrpc": "2.0",\n  "id": 1,\n  "result": {}\n}',
  );
});

test("braces inside a string do not change the depth", () => {
  assert.equal(indentJson('{"a":"}{"}'), '{\n  "a": "}{"\n}');
});

test("text that is not JSON is left alone", () => {
  assert.equal(indentJson("upstream returned 502"), "upstream returned 502");
});
