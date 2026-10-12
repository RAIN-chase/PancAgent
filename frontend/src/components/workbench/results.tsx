import { useState } from "react";
import { ArrowUpRight, Download, Search, FileSearch } from "lucide-react";
import {
  comparison,
  displayValue,
  downloadJson,
  object,
  rows,
  text,
  type JsonObject,
  type SavedResult,
} from "@/lib/workbench";

export type EvidenceSelection = { result: SavedResult; path: string };
export type Inspect = (selection: EvidenceSelection) => void;

export function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="wb-empty">
      <FileSearch
        size={26}
        aria-hidden
      />
      <p>{children}</p>
    </div>
  );
}

export function ResultTable({
  data,
  onInspect,
  basePath = "",
  result,
}: {
  data: JsonObject[];
  onInspect: Inspect;
  basePath?: string;
  result: SavedResult;
}) {
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const columns = [...new Set(data.flatMap((row) => Object.keys(row)))];
  const filtered = data
    .map((row, index) => ({ row, index }))
    .filter(
      ({ row }) =>
        !query ||
        JSON.stringify(row).toLowerCase().includes(query.toLowerCase()),
    );
  const pages = Math.max(1, Math.ceil(filtered.length / 20));
  const current = Math.min(page, pages - 1);
  if (!data.length) return <Empty>此结果未提供记录</Empty>;
  return (
    <div className="wb-data-table">
      <div className="wb-toolbar">
        <label className="wb-search">
          <Search size={15} />
          <input
            aria-label="筛选已载入记录"
            placeholder="筛选已载入记录"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(0);
            }}
          />
        </label>
        <span className="wb-muted">
          {filtered.length} / {data.length} 条
        </span>
      </div>
      <div className="wb-table-scroll">
        <table>
          <thead>
            <tr>
              {columns.map((key) => (
                <th key={key}>{key}</th>
              ))}
              <th>证据</th>
            </tr>
          </thead>
          <tbody>
            {filtered
              .slice(current * 20, (current + 1) * 20)
              .map(({ row, index }) => (
                <tr key={index}>
                  {columns.map((key) => (
                    <td key={key}>{displayValue(row[key])}</td>
                  ))}
                  <td>
                    <button
                      className="wb-icon"
                      title="查看字段来源"
                      aria-label={`查看第 ${index + 1} 条来源`}
                      onClick={() =>
                        onInspect({ result, path: `${basePath}/${index}` })
                      }
                    >
                      <ArrowUpRight size={16} />
                    </button>
                  </td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
      <div className="wb-pagination">
        <button
          disabled={current === 0}
          onClick={() => setPage(current - 1)}
        >
          上一页
        </button>
        <span>
          {current + 1} / {pages}
        </span>
        <button
          disabled={current + 1 >= pages}
          onClick={() => setPage(current + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}

export function MultiProtocolResult({
  result,
  onInspect,
}: {
  result: SavedResult;
  onInspect: Inspect;
}) {
  const value = comparison(result);
  const base = object(result.value.result_preview).tool_result
    ? "/result_preview/tool_result"
    : "/result_preview";
  const ids = Array.isArray(value.protocol_ids)
    ? value.protocol_ids.map(text)
    : [];
  const [tab, setTab] = useState("features");
  return (
    <>
      <div className="wb-toolbar">
        <h2>{ids.length} 套 Protocol 比较</h2>
        <button
          className="wb-icon"
          title="下载完整工具结果"
          aria-label="下载完整工具结果"
          onClick={() => downloadJson(result.value, `result_${result.id}.json`)}
        >
          <Download size={16} />
        </button>
      </div>
      <p className="wb-muted">{ids.join(" · ")}</p>
      <div
        className="wb-tabs"
        role="tablist"
        aria-label="Protocol 结果视图"
      >
        {[
          ["features", "特征差异"],
          ["steps", "步骤与事件"],
          ["alignment", "参照语义对齐"],
        ].map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === "features" && (
        <>
          <div className="wb-table-scroll">
            <table>
              <thead>
                <tr>
                  <th>特征</th>
                  {ids.map((id) => (
                    <th key={id}>{id}</th>
                  ))}
                  <th>来源</th>
                </tr>
              </thead>
              <tbody>
                {rows(value.feature_differences).map((row, index) => (
                  <tr key={text(row.feature)}>
                    <th>{text(row.feature)}</th>
                    {ids.map((id) => (
                      <td key={id}>{displayValue(object(row.values)[id])}</td>
                    ))}
                    <td>
                      <button
                        className="wb-icon"
                        aria-label={`查看第 ${index + 1} 条来源`}
                        title="查看字段来源"
                        onClick={() =>
                          onInspect({
                            result,
                            path: `${base}/feature_differences/${index}`,
                          })
                        }
                      >
                        <ArrowUpRight size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="wb-warning">
            展示 {rows(value.feature_differences).length} /{" "}
            {displayValue(value.total_differing_features)}{" "}
            个差异特征。未提供不填零；编译零值不代表原文明确未使用。
          </p>
        </>
      )}
      {tab === "steps" &&
        rows(object(value.event_step_diff).protocol_assessments).map(
          (row, index) => (
            <section
              className="wb-band"
              key={text(row.protocol_id)}
            >
              <h3>{text(row.protocol_id)}</h3>
              <ResultTable
                data={rows(row.events)}
                result={result}
                basePath={`${base}/event_step_diff/protocol_assessments/${index}/events`}
                onInspect={onInspect}
              />
              <details>
                <summary>完整步骤、语义与缺失证据</summary>
                <pre>{JSON.stringify(row, null, 2)}</pre>
              </details>
            </section>
          ),
        )}
      {tab === "alignment" && (
        <>
          <p className="wb-warning">
            参照方案：{text(object(value.event_step_diff).anchor_protocol_id)}
            。对齐不等于实验等价。
          </p>
          <pre>
            {JSON.stringify(
              object(value.event_step_diff).anchor_alignments,
              null,
              2,
            )}
          </pre>
        </>
      )}
    </>
  );
}

export function ProtocolResult({
  result,
  onInspect,
  onFollowup,
}: {
  result: SavedResult;
  onInspect: Inspect;
  onFollowup?: (question: string) => void;
}) {
  const [tab, setTab] = useState("features");
  const value = comparison(result);
  const base = object(result.value.result_preview).tool_result
    ? "/result_preview/tool_result"
    : "/result_preview";
  const a = object(value.protocol_a),
    b = object(value.protocol_b);
  const detail = object(value.event_step_diff);
  const diff = rows(value.feature_differences);
  return (
    <>
      <div className="wb-pair">
        <div>
          <small>PROTOCOL A</small>
          <h2>
            {text(object(a.metadata).protocol_name) ||
              text(a.protocol_id) ||
              "未提供"}
          </h2>
          <code>{text(a.protocol_id)}</code>
        </div>
        <span>↔</span>
        <div>
          <small>PROTOCOL B</small>
          <h2>
            {text(object(b.metadata).protocol_name) ||
              text(b.protocol_id) ||
              "未提供"}
          </h2>
          <code>{text(b.protocol_id)}</code>
        </div>
      </div>
      <div className="wb-toolbar wb-result-meta">
        <span className="wb-status">{displayValue(result.value.status)}</span>
        <code>{result.id}</code>
        <button
          className="wb-icon"
          title="下载完整工具结果"
          aria-label="下载完整工具结果"
          onClick={() => downloadJson(result.value, `result_${result.id}.json`)}
        >
          <Download size={16} />
        </button>
      </div>
      <div
        className="wb-tabs"
        role="tablist"
        aria-label="Protocol 结果视图"
      >
        {[
          ["features", "特征差异"],
          ["steps", "步骤与事件"],
          ["alignment", "语义对齐"],
        ].map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "features" && (
          <>
            <ResultTable
              result={result}
              data={diff}
              basePath={`${base}/feature_differences`}
              onInspect={onInspect}
            />
            <p className="wb-warning">
              矩阵零值不代表原文明确未使用。缺失与无法解析不填零；此表可能受后端
              max_features 限制。
            </p>
            {!!diff.length && onFollowup && (
              <button
                className="wb-link"
                onClick={() =>
                  onFollowup(
                    `针对 protocols ${text(a.protocol_id)} 与 ${text(b.protocol_id)}，结果 ${result.id}（输入版本 ${text(result.ref.input_version)}）中的特征差异，是否有成熟度证据支持解释？请保留反对证据和混杂限制，不作因果断言。`,
                  )
                }
              >
                结合成熟度继续追问 <ArrowUpRight size={14} />
              </button>
            )}
          </>
        )}
        {tab === "steps" && (
          <>
            {["a", "b"].map((side) => (
              <section
                className="wb-band"
                key={side}
              >
                <h3>Protocol {side.toUpperCase()} · 步骤与事件</h3>
                <ol
                  className="wb-timeline"
                  aria-label={`Protocol ${side.toUpperCase()} 事件时间线`}
                >
                  {rows(detail[`protocol_${side}_events`]).map(
                    (event, index) => (
                      <li key={index}>
                        <button
                          onClick={() =>
                            onInspect({
                              result,
                              path: `${base}/event_step_diff/protocol_${side}_events/${index}`,
                            })
                          }
                        >
                          <small>{displayValue(event.stage_id)}</small>
                          <strong>
                            {displayValue(
                              event.standardized_name || event.factor_name,
                            )}
                          </strong>
                          <span>
                            Day {displayValue(event.day_start)} →{" "}
                            {displayValue(event.day_end)}
                          </span>
                        </button>
                      </li>
                    ),
                  )}
                </ol>
                <ResultTable
                  result={result}
                  data={rows(detail[`protocol_${side}_events`])}
                  basePath={`${base}/event_step_diff/protocol_${side}_events`}
                  onInspect={onInspect}
                />
                <details>
                  <summary>完整结构化步骤</summary>
                  <pre>
                    {JSON.stringify(
                      detail[`protocol_${side}_steps`] ?? [],
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </section>
            ))}
            <p className="wb-muted">{displayValue(detail.detail_status)}</p>
          </>
        )}
        {tab === "alignment" && (
          <>
            <pre>
              {JSON.stringify(detail.semantic_alignment ?? {}, null, 2)}
            </pre>
            <button
              className="wb-link"
              onClick={() =>
                onInspect({ result, path: `${base}/event_step_diff` })
              }
            >
              检查对齐来源 <ArrowUpRight size={14} />
            </button>
          </>
        )}
      </div>
    </>
  );
}

export function DomainResult({
  result,
  onInspect,
}: {
  result: SavedResult;
  onInspect: Inspect;
}) {
  const preview = result.value.result_preview;
  const data = Array.isArray(preview)
    ? rows(preview)
    : rows(object(preview).hypotheses);
  const path = Array.isArray(preview)
    ? "/result_preview"
    : "/result_preview/hypotheses";
  return (
    <>
      <div className="wb-toolbar wb-result-meta">
        <code>{result.action}</code>
        <span className="wb-status">{displayValue(result.value.status)}</span>
        <button
          className="wb-icon"
          title="下载完整工具结果"
          aria-label="下载完整工具结果"
          onClick={() => downloadJson(result.value, `result_${result.id}.json`)}
        >
          <Download size={16} />
        </button>
      </div>
      {data.length ? (
        <ResultTable
          data={data}
          result={result}
          basePath={path}
          onInspect={onInspect}
        />
      ) : (
        <>
          <pre>{JSON.stringify(preview ?? {}, null, 2)}</pre>
          <button
            className="wb-link"
            onClick={() => onInspect({ result, path: "/result_preview" })}
          >
            查看结果来源 <ArrowUpRight size={14} />
          </button>
        </>
      )}
      <section className="wb-band">
        <h3>验证与限制</h3>
        <pre>
          {JSON.stringify(
            {
              validation: result.value.validation,
              limitations: result.value.limitations,
              confidence_caveats: result.value.confidence_caveats,
            },
            null,
            2,
          )}
        </pre>
      </section>
      {result.action.startsWith("hypothesis") && (
        <p className="wb-warning">
          候选为待验证假说；效用或排序不是实验成功概率。
        </p>
      )}
    </>
  );
}
