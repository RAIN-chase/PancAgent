/** Read-only adapters for saved contracts. Never derive scientific values from Markdown. */
export type JsonObject = Record<string, unknown>;
export type ResearchScope = Record<string, string[]>;
export const scopeFields = [
  ["protocol_ids", "Protocol IDs"],
  ["protocol_versions", "Protocol 版本（ID=版本）"],
  ["dataset_ids", "数据集 IDs"],
  ["dataset_versions", "数据集版本（ID=版本）"],
  ["endpoints", "终点"],
  ["cell_types", "细胞类型"],
  ["stages", "阶段"],
  ["user_constraints", "用户约束"],
] as const;
export function scopeMemoryUpdate(
  value: ResearchScope,
  previous: JsonObject,
  edited: boolean,
): ResearchScope {
  if (!edited) return {};
  const update = Object.fromEntries(
    [
      "protocol_ids",
      "protocol_versions",
      "endpoints",
      "stages",
      "user_constraints",
    ]
      .filter(
        (key) =>
          JSON.stringify(value[key] ?? []) !==
          JSON.stringify(previous[key] ?? []),
      )
      .map((key) => [key, value[key] ?? []]),
  );
  if ("user_constraints" in update)
    update.remove_constraints = (
      Array.isArray(previous.user_constraints) ? previous.user_constraints : []
    ).filter((item: string) => !(value.user_constraints ?? []).includes(item));
  return update;
}
export type SavedResult = {
  id: string;
  runId: string;
  action: string;
  value: JsonObject;
  ref: JsonObject;
};
export type Snapshot = { results: SavedResult[]; reports: JsonObject[] };
export const object = (value: unknown): JsonObject =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : {};
export const rows = (value: unknown): JsonObject[] =>
  Array.isArray(value) ? value.map(object) : [];
export const text = (value: unknown): string =>
  value == null ? "" : String(value);
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "未提供";
  if (value === "unparseable") return "无法解析";
  if (value === "not_applicable") return "不适用";
  if (value === "explicit_not_used") return "明确未使用";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}
export function pointer(value: unknown, path: string): unknown {
  if (path === "") return value;
  if (!path.startsWith("/")) throw new Error("无效字段引用");
  return path
    .slice(1)
    .split("/")
    .reduce<unknown>((item, part) => {
      if (/~(?![01])/.test(part)) throw new Error("无效字段引用");
      const key = part.replace(/~1/g, "/").replace(/~0/g, "~");
      if (
        Array.isArray(item) &&
        /^(0|[1-9][0-9]*)$/.test(key) &&
        Number(key) < item.length
      )
        return item[Number(key)];
      const obj = object(item);
      if (Object.hasOwn(obj, key)) return obj[key];
      throw new Error("引用的字段未载入");
    }, value);
}
export function snapshot(messages: unknown[]): Snapshot {
  const results: SavedResult[] = [],
    reports: JsonObject[] = [];
  for (const entry of messages) {
    const message = object(entry);
    const report = object(object(message.additional_kwargs).analysis_report);
    if (report.schema_version === "scientific-analysis-report-v3")
      reports.push(report);
    if (message.type !== "tool") continue;
    try {
      const content =
        typeof message.content === "string"
          ? message.content
          : rows(message.content)
              .filter((block) => block.type === "text")
              .map((block) => text(block.text))
              .join("");
      const value = object(JSON.parse(content));
      if (value.schema_version !== "orchestrator-evidence-envelope-v2")
        continue;
      const ref = rows(value.result_refs)[0] ?? {};
      if (!ref.result_id) continue;
      results.push({
        id: text(ref.result_id),
        runId: text(value.run_id),
        action: text(value.selected_operation),
        value,
        ref,
      });
    } catch {
      /* Incomplete streamed JSON is not a saved result. */
    }
  }
  return {
    results: [
      ...new Map(results.map((result) => [result.id, result])).values(),
    ],
    reports,
  };
}
export function readArchive(value: unknown): Snapshot {
  if (Array.isArray(value)) return snapshot(value);
  const data = object(value);
  if (Array.isArray(data.messages)) return snapshot(data.messages);
  if (data.schema_version === "scientific-analysis-report-v3")
    return { results: [], reports: [data] };
  if (data.schema_version === "orchestrator-evidence-envelope-v2") {
    return snapshot([{ type: "tool", content: JSON.stringify(data) }]);
  }
  throw new Error(
    "仅支持归档 messages、final_report.json 或工具 evidence envelope；不接受 h5ad。",
  );
}
export function comparison(result: SavedResult): JsonObject {
  const preview = object(result.value.result_preview);
  return object(preview.tool_result ?? preview);
}
export function protocols(results: SavedResult[]): JsonObject[] {
  const catalog = new Map<string, JsonObject>();
  for (const result of results) {
    const value = comparison(result);
    for (const row of [
      object(value.protocol_a),
      object(value.protocol_b),
      ...rows(value.matches),
      ...rows(value.results),
      ...rows(value.protocols),
      ...rows(value.sample_protocols),
      ...rows(value.protocol_assessments),
    ]) {
      if (row.protocol_id)
        catalog.set(text(row.protocol_id), { ...object(row.metadata), ...row });
    }
  }
  return [...catalog.values()];
}
export function downloadJson(value: unknown, name: string) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = name.replace(/[^a-zA-Z0-9_.-]/g, "_");
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
