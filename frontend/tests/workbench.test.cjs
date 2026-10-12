const assert = require("node:assert/strict");
const test = require("node:test");
const path = require("node:path");
const { buildSync } = require("esbuild");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const fixture = require("./fixtures/workbench.cjs");
function load(file) {
  const code = buildSync({
    entryPoints: [path.join(__dirname, "../src", file)],
    bundle: true,
    write: false,
    platform: "node",
    format: "cjs",
    packages: "external",
    jsx: "automatic",
  }).outputFiles[0].text;
  const module = { exports: {} };
  new Function("require", "module", "exports", code)(
    require,
    module,
    module.exports,
  );
  return module.exports;
}
const adapter = load("lib/workbench.ts");
test("only explicitly changed scope updates memory; blank defaults do not erase question targets", () => {
  assert.deepEqual(adapter.scopeMemoryUpdate({}, {}, false), {});
  assert.deepEqual(adapter.scopeMemoryUpdate({}, {}, true), {});
  assert.deepEqual(
    adapter.scopeMemoryUpdate(
      { endpoints: ["maturity"] },
      { endpoints: ["maturity"] },
      true,
    ),
    {},
  );
  assert.deepEqual(
    adapter.scopeMemoryUpdate(
      {},
      { endpoints: ["composition"], user_constraints: ["only beta"] },
      true,
    ),
    {
      endpoints: [],
      user_constraints: [],
      remove_constraints: ["only beta"],
    },
  );
});
const { ProtocolResult, MultiProtocolResult } = load(
  "components/workbench/results.tsx",
);

test("multi-protocol table renders every selected object and exact values", () => {
  const result = structuredClone(adapter.snapshot(fixture.messages).results[0]);
  result.value.result_preview.tool_result = {
    protocol_ids: ["a", "b", "c"],
    protocols: ["a", "b", "c"].map((protocol_id) => ({ protocol_id })),
    feature_differences: [
      { feature: "dose", values: { a: "0", b: "3.000", c: "" } },
    ],
    total_differing_features: 1,
    event_step_diff: {},
  };
  assert.equal(adapter.protocols([result]).length, 3);
  const html = renderToStaticMarkup(
    React.createElement(MultiProtocolResult, { result, onInspect() {} }),
  );
  for (const expected of [
    "3 套 Protocol 比较",
    "3.000",
    "未提供",
    ">0<",
    "<th>c</th>",
  ])
    assert.ok(html.includes(expected), expected);
});
test("saved JSON is the authority; preserve zero, decimal strings and missing states", () => {
  const before = JSON.stringify(fixture.messages);
  const data = adapter.snapshot(fixture.messages);
  assert.equal(data.results.length, 1);
  assert.equal(data.reports[0].run_id, "fixture-run");
  assert.equal(adapter.protocols(data.results).length, 2);
  assert.equal(
    adapter.pointer(
      data.results[0].value,
      "/result_preview/tool_result/feature_differences/0/protocol_b_value",
    ),
    "3.000",
  );
  for (const [value, expected] of [
    [0, "0"],
    [null, "未提供"],
    ["unparseable", "无法解析"],
    ["not_applicable", "不适用"],
    ["explicit_not_used", "明确未使用"],
  ])
    assert.equal(adapter.displayValue(value), expected);
  assert.equal(JSON.stringify(fixture.messages), before);
});
test("no Markdown measurements or partial tool JSON are accepted", () => {
  assert.equal(
    adapter.snapshot([
      { type: "ai", content: "maturity: 0.95" },
      { type: "tool", content: '{"schema_version":' },
    ]).results.length,
    0,
  );
  assert.throws(() =>
    adapter.readArchive({ h5ad_path: "/server/private.h5ad" }),
  );
  assert.throws(() => adapter.pointer({ a: [] }, "/a/01"));
  assert.throws(() => adapter.pointer({}, "/__proto__"));
});
test("report without loaded result does not fabricate a result", () => {
  const data = adapter.readArchive(fixture.report);
  assert.equal(data.results.length, 0);
  assert.equal(data.reports.length, 1);
  assert.deepEqual(
    adapter.readArchive(fixture.messages),
    adapter.snapshot(fixture.messages),
  );
});
test("protocol table renders exact source values and missing status", () => {
  const html = renderToStaticMarkup(
    React.createElement(ProtocolResult, {
      result: adapter.snapshot(fixture.messages).results[0],
      onInspect() {},
      onFollowup() {},
    }),
  );
  for (const expected of [
    "Protocol Alpha",
    "Protocol Beta",
    "3.000",
    "未提供",
    "无法解析",
    "fixture-result",
  ])
    assert.ok(html.includes(expected), expected);
  assert.ok(!html.includes("/server/"));
});
