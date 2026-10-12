const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { createRequire } = require("node:module");
const path = require("node:path");
const test = require("node:test");
const { transformSync } = require("esbuild");
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const root = path.resolve(__dirname, "..");
const file = path.join(root, "src/components/thread/analysis-progress.tsx");
const { code } = transformSync(readFileSync(file, "utf8"), {
  loader: "tsx",
  format: "cjs",
  jsx: "automatic",
});
const compiled = { exports: {} };
new Function("require", "module", "exports", code)(
  createRequire(file),
  compiled,
  compiled.exports,
);
const { AnalysisProgress } = compiled.exports;

test("conversation and clarification render no scientific analysis panel", () => {
  for (const stop_reason of ["conversation", "needs_clarification"]) {
    const html = renderToStaticMarkup(
      React.createElement(AnalysisProgress, {
        values: { messages: [], plan: { steps: [] }, stop_reason },
      }),
    );
    assert.equal(html, "");
  }
});

test("real analysis retains its structured report download", () => {
  const html = renderToStaticMarkup(
    React.createElement(AnalysisProgress, {
      values: {
        plan: { steps: [{ id: "s1", action_id: "protocol.compare" }] },
        messages: [
          {
            type: "ai",
            additional_kwargs: {
              analysis_report: {
                schema_version: "scientific-analysis-report-v3",
                quantitative_bindings: [],
              },
            },
          },
        ],
      },
    }),
  );
  assert.match(html, /Analysis progress/);
  assert.match(html, /Download saved report JSON/);
});

test("raw tool displays are opt-in consistently across controls and messages", () => {
  for (const file of [
    "src/components/thread/index.tsx",
    "src/components/thread/messages/ai.tsx",
  ]) {
    assert.match(
      readFileSync(path.join(root, file), "utf8"),
      /"hideToolCalls",\s*parseAsBoolean\.withDefault\(true\)/,
    );
  }
});
