// Run against a local dev server. LangGraph transport is mocked, never a real analysis.
const { chromium } = require("@playwright/test");
const assert = require("node:assert/strict");
const { readFile, mkdir } = require("node:fs/promises");
const path = require("node:path");
const fixture = require("./fixtures/workbench.cjs");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  });
  const output = path.resolve(__dirname, "../../.tmp/workbench-ui");
  await mkdir(output, { recursive: true });
  const errors = [];
  try {
    for (const [name, width, height] of [
      ["desktop", 1440, 1000],
      ["mobile", 390, 844],
    ]) {
      const page = await browser.newPage({
        viewport: { width, height },
        acceptDownloads: true,
      });
      page.on("pageerror", (error) => errors.push(error.message));
      let submissions = 0;
      await page.route("**/api/data-catalog?**", (route) =>
        route.fulfill({
          json: {
            configured: true,
            collections: [
              {
                role: "public_organoid",
                collection_id: "public",
                name: "Organoids",
                expected_count: 51,
                status: "transferring",
              },
            ],
            datasets: [
              {
                dataset_id: "atlas",
                role: "reference_atlas",
                status: "ready",
                analysis_available: true,
                version: "v1",
              },
              {
                dataset_id: "incoming",
                role: "public_organoid",
                status: "transferring",
                analysis_available: false,
              },
            ],
          },
        }),
      );
      await page.route("http://127.0.0.1:2024/**", async (route) => {
        if (route.request().url().includes("/runs")) submissions++;
        await route.fulfill({
          json: route.request().url().includes("search") ? [] : {},
          headers: { "Access-Control-Allow-Origin": "*" },
        });
      });
      await page.goto(
        `${process.env.WORKBENCH_URL ?? "http://127.0.0.1:3100"}/?apiUrl=http%3A%2F%2F127.0.0.1%3A2024&assistantId=orchestrator&authScheme=local`,
      );
      await page
        .getByRole("heading", { name: "Protocol 比较", exact: true })
        .waitFor();
      await page.getByRole("button", { name: "收起对话", exact: true }).click();
      await page.getByRole("button", { name: "提出问题", exact: true }).click();
      await page.getByRole("textbox", { name: "研究问题" }).waitFor();
      await page.getByRole("button", { name: "收起对话", exact: true }).click();
      await page
        .getByRole("button", { name: "项目与数据", exact: true })
        .click();
      await page
        .getByRole("button", { name: "探索 atlas", exact: true })
        .waitFor();
      assert.equal(
        await page
          .getByRole("button", { name: "探索 incoming", exact: true })
          .isDisabled(),
        true,
      );
      await page
        .getByRole("button", { name: "探索 atlas", exact: true })
        .click();
      assert.ok(
        (
          await page.getByRole("textbox", { name: "研究问题" }).inputValue()
        ).includes('request.dataset_id="atlas"'),
      );
      await page
        .getByRole("textbox", { name: "研究问题" })
        .fill("继续研究同一问题");
      for (const tab of ["表型分析", "假说与方案", "Protocol 比较"]) {
        await page.getByRole("button", { name: tab, exact: true }).click();
        assert.equal(
          await page.getByRole("textbox", { name: "研究问题" }).inputValue(),
          "继续研究同一问题",
        );
        assert.ok(
          (
            await page.getByRole("region", { name: "研究范围" }).innerText()
          ).includes("atlas=v1"),
        );
      }
      await page.screenshot({
        path: path.join(output, `${name}-shared-chat.png`),
        fullPage: true,
      });
      await page.getByRole("button", { name: "收起对话", exact: true }).click();
      await page.screenshot({
        path: path.join(output, `${name}-datasets.png`),
        fullPage: true,
      });
      await page
        .getByRole("button", { name: "Protocol 比较", exact: true })
        .click();
      await page.screenshot({
        path: path.join(output, `${name}-empty.png`),
        fullPage: true,
      });
      await page.locator('input[type="file"]').setInputFiles({
        name: "contract-fixture.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify({ messages: fixture.messages })),
      });
      await page
        .getByRole("heading", { name: "Protocol Alpha", exact: true })
        .waitFor();
      await page.getByLabel("选择 fixture-A", { exact: true }).check();
      await page.getByLabel("选择 fixture-B", { exact: true }).check();
      await page.getByRole("button", { name: "查看比较", exact: true }).click();
      assert.equal(
        await page.getByRole("cell", { name: "3.000", exact: true }).count(),
        1,
      );
      await page.getByLabel("筛选已载入记录").fill("unknown_dose");
      assert.equal(
        await page
          .getByRole("cell", { name: "factor_duration", exact: true })
          .count(),
        0,
      );
      await page.getByLabel("筛选已载入记录").fill("");
      await page.getByRole("button", { name: "查看第 1 条来源" }).click();
      await page.getByRole("complementary", { name: "来源证据" }).waitFor();
      assert.ok(
        (
          await page
            .getByRole("complementary", { name: "来源证据" })
            .innerText()
        ).includes("fixture-input-v1"),
      );
      await page.screenshot({
        path: path.join(output, `${name}-evidence.png`),
        fullPage: true,
      });
      await page.getByRole("button", { name: "关闭证据", exact: true }).click();
      await page.getByRole("tab", { name: "步骤与事件" }).click();
      await page.getByRole("list", { name: "Protocol A 事件时间线" }).waitFor();
      await page.screenshot({
        path: path.join(output, `${name}-steps.png`),
        fullPage: true,
      });
      const downloading = page.waitForEvent("download");
      await page
        .getByRole("button", { name: "下载完整工具结果", exact: true })
        .click();
      const download = await downloading;
      assert.deepEqual(
        JSON.parse(await readFile(await download.path(), "utf8")),
        fixture.envelope,
      );
      await page.getByText("支持、反对证据与限制", { exact: true }).click();
      assert.ok(
        (await page.locator(".wb-conclusion").innerText()).includes(
          "fixture-counterevidence",
        ),
      );
      await page
        .getByText("定量字段引用 · 展示 1 / 1", { exact: true })
        .click();
      assert.ok(
        (await page.locator(".wb-band").last().innerText()).includes("3.000"),
      );
      await page
        .getByRole("button", { name: "运行与历史", exact: true })
        .click();
      assert.equal(
        await page
          .getByRole("button", { name: "下载报告 JSON", exact: true })
          .count(),
        1,
      );
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      );
      assert.equal(overflow, false, `${name} page overflows`);
      assert.equal(submissions, 0, "Browsing must not create runs");
      await page
        .getByRole("button", { name: "Protocol 比较", exact: true })
        .click();
      const many = structuredClone(fixture.envelope);
      many.result_preview.tool_result = {
        protocol_ids: ["fixture-A", "fixture-B", "fixture-C"],
        protocols: ["fixture-A", "fixture-B", "fixture-C"].map(
          (protocol_id) => ({ protocol_id }),
        ),
        feature_differences: [
          {
            feature: "dose",
            values: { "fixture-A": "0", "fixture-B": "3.000", "fixture-C": "" },
          },
        ],
        total_differing_features: 1,
        event_step_diff: {
          protocol_assessments: [
            {
              protocol_id: "fixture-C",
              events: [{ evidence_quote: "third source" }],
              structured_steps: [],
            },
          ],
        },
      };
      await page.locator('input[type="file"]').setInputFiles({
        name: "many.json",
        mimeType: "application/json",
        buffer: Buffer.from(JSON.stringify(many)),
      });
      await page.getByRole("heading", { name: "3 套 Protocol 比较" }).waitFor();
      for (const id of ["fixture-A", "fixture-B", "fixture-C"])
        await page.getByLabel(`选择 ${id}`, { exact: true }).check();
      await page.getByRole("button", { name: "查看比较", exact: true }).click();
      assert.equal(
        await page.getByRole("cell", { name: "3.000", exact: true }).count(),
        1,
      );
      await page.getByRole("button", { name: "查看第 1 条来源" }).click();
      assert.ok(
        (
          await page
            .getByRole("complementary", { name: "来源证据" })
            .innerText()
        ).includes('"fixture-C": ""'),
      );
      await page.getByRole("button", { name: "关闭证据", exact: true }).click();
      await page.screenshot({
        path: path.join(output, `${name}-multi.png`),
        fullPage: true,
      });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
      await page.getByRole("tab", { name: "步骤与事件" }).click();
      assert.equal(
        await page
          .getByRole("cell", { name: "third source", exact: true })
          .count(),
        1,
      );
      await page.getByRole("button", { name: "返回会话", exact: true }).click();
      await page
        .getByRole("button", { name: "新建会话", exact: true })
        .last()
        .click();
      await page.getByRole("textbox", { name: "研究问题" }).waitFor();
      assert.equal(
        await page
          .getByRole("heading", { name: "Protocol Alpha", exact: true })
          .count(),
        0,
      );
      await page.close();
    }
    // Exercise the installed LangGraph SDK rather than a second client implementation.
    const page = await browser.newPage({
      viewport: { width: 1440, height: 1000 },
    });
    page.on("pageerror", (error) => errors.push(error.message));
    const threadId = "11111111-1111-4111-8111-111111111111";
    const runId = "22222222-2222-4222-8222-222222222222";
    let submitted = 0,
      joined = 0,
      stored = null;
    const headers = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Expose-Headers": "Content-Location",
      "Content-Location": `/threads/${threadId}/runs/${runId}`,
    };
    await page.route("http://127.0.0.1:2024/**", async (route) => {
      const url = route.request().url();
      if (url.endsWith("/threads") && route.request().method() === "POST")
        return route.fulfill({ json: { thread_id: threadId }, headers });
      if (url.includes("/runs/") && url.includes("/stream")) {
        if (route.request().method() === "POST") {
          submitted++;
          const request = route.request().postDataJSON();
          assert.equal(request.on_disconnect, "continue");
          assert.deepEqual(
            request.input.request.workbench_context.protocol_ids,
            ["fixture-A", "fixture-B"],
          );
          assert.deepEqual(request.input.request.workbench_context.endpoints, [
            "maturity",
          ]);
          if (submitted === 2) {
            const ref = JSON.parse(
              request.input.request.workbench_context.result_refs[0],
            );
            assert.equal(ref.input_version, "fixture-input-v1");
            assert.ok(ref.field_path.startsWith("/"));
            assert.ok(ref.result_id);
          }
          stored = {
            project_id: "fixture-project",
            request: request.input.request,
            messages: [
              ...request.input.messages,
              ...fixture.messages.map((message, index) => ({
                ...message,
                id: `fixture-msg-${index}`,
              })),
            ],
            plan: {
              question: "Fixture comparison",
              steps: [
                {
                  id: "s1",
                  action_id: "protocol.compare",
                  depends_on: [],
                  expected_outputs: [],
                },
              ],
            },
            step_results: [
              { step_id: "s1", status: "ok", usable: true, executed: true },
            ],
            stop_reason: "completed",
          };
        } else joined++;
        return route.fulfill({
          contentType: "text/event-stream",
          headers,
          body: `event: metadata\ndata: ${JSON.stringify({ run_id: runId })}\n\nevent: values\ndata: ${JSON.stringify(stored)}\n\nevent: end\ndata: {}\n\n`,
        });
      }
      if (url.endsWith("/history"))
        return route.fulfill({
          json: stored
            ? [
                {
                  values: stored,
                  checkpoint: {
                    thread_id: threadId,
                    checkpoint_id: "fixture-checkpoint",
                    checkpoint_ns: "",
                  },
                  next: [],
                  tasks: [],
                  parent_checkpoint: null,
                  metadata: {},
                  created_at: "2026-10-12T00:00:00Z",
                },
              ]
            : [],
          headers,
        });
      return route.fulfill({ json: url.includes("search") ? [] : {}, headers });
    });
    await page.goto(
      `${process.env.WORKBENCH_URL ?? "http://127.0.0.1:3100"}/?apiUrl=http%3A%2F%2F127.0.0.1%3A2024&assistantId=orchestrator&authScheme=local&chat=true`,
    );
    await page.getByRole("button", { name: "编辑研究范围" }).click();
    const scopeProtocols = page.getByRole("textbox", {
      name: "Protocol IDs",
      exact: true,
    });
    await scopeProtocols.fill("fixture-A");
    await scopeProtocols.press("End");
    await scopeProtocols.press("Enter");
    await scopeProtocols.pressSequentially("fixture-B");
    assert.equal(await scopeProtocols.inputValue(), "fixture-A\nfixture-B");
    await page
      .getByRole("textbox", { name: "终点", exact: true })
      .fill("maturity");
    await page.getByRole("button", { name: "应用范围", exact: true }).click();
    await page
      .getByRole("textbox", { name: "研究问题" })
      .fill("比较 fixture-A 和 fixture-B");
    await page.locator(".wb-composer").evaluate((form) => {
      form.requestSubmit();
      form.requestSubmit();
    });
    await page
      .getByRole("heading", { name: "Protocol Alpha", exact: true })
      .waitFor();
    assert.equal(submitted, 1);
    await page.screenshot({
      path: path.join(output, "desktop-live-result.png"),
      fullPage: true,
    });
    await page.getByRole("button", { name: "收起对话", exact: true }).click();
    await page
      .getByRole("button", { name: "结合成熟度继续追问", exact: false })
      .click();
    assert.ok(
      (
        await page.getByRole("textbox", { name: "研究问题" }).inputValue()
      ).includes("fixture-input-v1"),
    );
    assert.equal(submitted, 1, "Follow-up draft must not automatically submit");
    assert.ok(
      (await page.locator(".wb-followup").innerText()).includes(
        "fixture-input-v1",
      ),
    );
    await page.getByRole("button", { name: "表型分析", exact: true }).click();
    await page.getByRole("button", { name: "假说与方案", exact: true }).click();
    assert.ok(
      (await page.locator(".wb-followup").innerText()).includes(
        "fixture-input-v1",
      ),
    );
    assert.ok(
      (
        await page.getByRole("region", { name: "研究范围" }).innerText()
      ).includes("maturity"),
    );
    await page.evaluate(
      ({ threadId, runId }) =>
        sessionStorage.setItem(`lg:stream:${threadId}`, runId),
      { threadId, runId },
    );
    await page.reload();
    await page
      .getByRole("heading", { name: "Protocol Alpha", exact: true })
      .waitFor();
    await page.waitForFunction(
      ({ threadId }) =>
        sessionStorage.getItem(`lg:stream:${threadId}`) === null,
      { threadId },
    );
    assert.equal(submitted, 1, "Reconnect must not create another run");
    assert.equal(joined, 1, "Reconnect joins existing run");
    const closeChat = page.getByRole("button", { name: "收起对话", exact: true });
    if (await closeChat.isVisible()) await closeChat.click();
    await page.getByRole("button", { name: "查看第 1 条来源" }).click();
    await page
      .getByRole("button", { name: "基于此字段追问", exact: true })
      .click();
    await page.getByRole("button", { name: "提交问题", exact: true }).click();
    await page.waitForFunction(() =>
      document
        .querySelector(".wb-messages")
        ?.textContent.includes("请解释此结果"),
    );
    await page.getByRole("button", { name: "提交问题", exact: true }).waitFor();
    assert.equal(submitted, 2);
    await page
      .getByRole("button", { name: "新建会话", exact: true })
      .last()
      .click();
    await page.getByRole("textbox", { name: "研究问题" }).waitFor();
    assert.equal(
      await page
        .getByRole("heading", { name: "Protocol Alpha", exact: true })
        .count(),
      0,
    );
    await page.close();
    assert.deepEqual(errors, [], "Browser runtime errors");
    console.log(
      "Desktop/mobile import, provenance, source steps, filtering, exact download, no unintended runs, and scope reset passed.",
    );
    console.log(`Screenshots: ${output}`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
