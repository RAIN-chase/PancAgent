// Run with node evals/planned_analysis/verify_progress.mjs; no backend or API keys required.
import assert from "node:assert/strict";
import { mkdir, readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const frontend = resolve(root, "frontend");
const require = createRequire(resolve(frontend, "package.json"));
const { build } = require("esbuild");
const { chromium } = require("@playwright/test");
const postcss = require("postcss");
const tailwind = require("@tailwindcss/postcss");
const output = resolve(root, "runs/planned_analysis/ui_checks");
await mkdir(output, { recursive: true });
const component = resolve(frontend, "src/components/thread/analysis-progress.tsx");
const bundle = await build({
  stdin: {
    contents: `import {createRoot} from 'react-dom/client';
      import {AnalysisProgress} from ${JSON.stringify(component)};
      const root = createRoot(document.getElementById('root'));
      window.renderAnalysis = (values) => root.render(<AnalysisProgress values={values}/>);`,
    resolveDir: frontend, loader: "tsx",
  },
  jsx: "automatic", bundle: true, write: false, format: "iife",
});
const cssPath = resolve(frontend, "src/app/globals.css");
const css = await postcss([tailwind({ base: frontend })]).process(await readFile(cssPath, "utf8"), { from: cssPath });
const report = { schema_version: "scientific-analysis-report-v2", run_id: "ui_fixture",
  quantitative_bindings: [{ result_id: "result_with_a_very_long_identifier", field_path: "/result_preview/0/mean_expression" }] };
const values = { messages: [{ type: "ai", content: "INS: 0.125", additional_kwargs: { analysis_report: report } }],
  plan: { question: "INS expression", evidence_gaps: ["single_study_driven:study1", "expression_scale_unknown"], steps: [
  { id: "s1", action_id: "protocol.assess" }, { id: "s2", action_id: "phenotype.gene_expression" }] },
  step_results: [{ step_id: "s1", usable: true, status: "ok" }, { step_id: "s2", usable: false, status: "partial" }],
  stop_reason: "evidence_insufficient:unresolved_dependencies" };
const browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHANNEL ? { channel: process.env.PLAYWRIGHT_CHANNEL } : {}) });
try {
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport, acceptDownloads: true });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setContent(`<style>${css.css}</style><main id="root" style="max-width:900px;margin:auto;padding:16px"></main>`);
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate((state) => window.renderAnalysis(state), values);
    await page.getByRole("status").waitFor();
    assert.match(await page.getByRole("status").innerText(), /evidence_insufficient/);
    await page.getByText("Evidence gaps (2)", { exact: true }).click();
    await page.getByText("Bound results (1)", { exact: true }).click();
    assert.match(await page.getByRole("cell").innerText(), /mean_expression/);
    assert.equal("value" in report.quantitative_bindings[0], false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Download saved report JSON" }).click();
    const download = await downloadPromise;
    const path = resolve(output, `report-${viewport.width}.json`);
    await download.saveAs(path);
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")), report);
    await page.screenshot({ path: resolve(output, `progress-${viewport.width}.png`), fullPage: true });
    await page.evaluate((state) => window.renderAnalysis(state), { ...values, messages: [], step_results: [], stop_reason: "budget_exhausted:tool_calls" });
    await page.getByText("not_executed", { exact: true }).first().waitFor();
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("Progress UI passed: desktop/mobile, stop state, no overflow, result references and report download.");
} finally {
  await browser.close();
}
