"use client";

import { useEffect, useRef, useState } from "react";
import { parseAsBoolean, useQueryState } from "nuqs";
import {
  Activity,
  ArrowUpRight,
  Database,
  Download,
  FlaskConical,
  FolderOpen,
  GitCompareArrows,
  History,
  MessageSquare,
  Microscope,
  RefreshCw,
  SquarePen,
  Upload,
  X,
} from "lucide-react";
import { useStreamContext } from "@/providers/Stream";
import { useThreads } from "@/providers/Thread";
import { Thread } from "@/components/thread";
import { AnalysisProgress } from "@/components/thread/analysis-progress";
import {
  comparison,
  displayValue,
  downloadJson,
  object,
  pointer,
  protocols,
  readArchive,
  rows,
  snapshot,
  text,
  scopeFields,
  scopeMemoryUpdate,
  type ResearchScope,
  type Snapshot,
} from "@/lib/workbench";
import {
  DomainResult,
  Empty,
  ProtocolResult,
  MultiProtocolResult,
  type EvidenceSelection,
} from "./results";
import { DataCatalog } from "./data-catalog";
import { ResearchScopeBar } from "./research-scope";

const views = [
  { id: "project", label: "项目与数据", Icon: Database },
  { id: "protocol", label: "Protocol 比较", Icon: GitCompareArrows },
  { id: "phenotype", label: "表型分析", Icon: Activity },
  { id: "hypothesis", label: "假说与方案", Icon: FlaskConical },
  { id: "runs", label: "运行与历史", Icon: History },
];

export function Workbench() {
  const [threadId] = useQueryState("threadId");
  const stream = useStreamContext();
  // Imported files, selections and drafts never survive a thread scope change.
  return (
    <WorkbenchSession
      key={`${stream.values.project_id ?? "default"}:${threadId ?? "new"}`}
    />
  );
}

function WorkbenchSession() {
  const stream = useStreamContext();
  const [threadId, setThreadId] = useQueryState("threadId");
  const { getThreads, threads, setThreads, threadsLoading, setThreadsLoading } =
    useThreads();
  const [view, setView] = useState("protocol");
  const [chat, setChat] = useQueryState(
    "chat",
    parseAsBoolean.withDefault(true),
  );
  const [input, setInput] = useState("");
  const [researchScope, setResearchScope] = useState<ResearchScope | null>(
    null,
  );
  const [followup, setFollowup] = useState<EvidenceSelection | null>(null);
  const [archive, setArchive] = useState<Snapshot | null>(null);
  const [archiveName, setArchiveName] = useState("");
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [protocolFilter, setProtocolFilter] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const live = snapshot(stream.messages);
  const data = archive ?? live;
  const catalog = protocols(data.results);
  const datasets = data.results.flatMap((result) =>
    rows(
      result.value.dataset_summaries ??
        object(result.value.result_preview).dataset_summaries,
    ).map((row, index) => ({ row, result, index })),
  );
  const session = object(object(stream.values.request).session_summary);
  const activeScope =
    researchScope ??
    Object.fromEntries(
      scopeFields.map(([key]) => [
        key,
        (session[key] ??
          stream.values.request?.workbench_context?.[key] ??
          []) as string[],
      ]),
    );
  const memoryUpdate = scopeMemoryUpdate(
    activeScope,
    session,
    researchScope !== null,
  );
  const submissionRequest = {
    memory_update: memoryUpdate,
    workbench_context: {
      ...activeScope,
      result_refs: followup
        ? [
            JSON.stringify({
              result_id: followup.result.id,
              run_id: followup.result.runId,
              field_path: followup.path,
              input_version: followup.result.ref.input_version,
              sha256: followup.result.ref.sha256,
            }),
          ]
        : [],
    },
  };
  const scope = object(session.scope);
  const projectId = archive
    ? "本地归档"
    : text(scope.project_id || stream.values.project_id) || "default";
  const relevant = data.results.filter((result) =>
    view === "protocol"
      ? result.action.startsWith("protocol.")
      : view === "phenotype"
        ? result.action.startsWith("phenotype.") ||
          result.action === "data.assess"
        : view === "hypothesis"
          ? ["hypothesis.generate", "experiment.propose"].includes(
              result.action,
            )
          : true,
  );
  const selected =
    relevant.find((result) => result.id === selectedId) ?? relevant.at(-1);
  const latestReport = selected
    ? data.reports.findLast((report) => report.run_id === selected.runId)
    : data.reports.at(-1);
  const boundValues = rows(latestReport?.display_quantitative_bindings).map(
    (binding) => {
      const result = data.results.find((item) => item.id === binding.result_id);
      try {
        return {
          binding,
          result,
          value: result
            ? pointer(result.value, text(binding.field_path))
            : "引用结果未载入",
        };
      } catch {
        return { binding, result: undefined, value: "引用字段未载入" };
      }
    },
  );
  const ask = (question: string) => {
    if (!archive) {
      setInput(question);
      setChat(true);
    }
  };
  const inspect = (selection: EvidenceSelection) => {
    setEvidence(selection);
    setChat(false);
  };
  const askResult = (
    selection: EvidenceSelection,
    question = "请解释此结果的依据、反对证据及局限。",
  ) => {
    setFollowup(selection);
    setEvidence(null);
    ask(question);
  };
  const refresh = async () => {
    setThreadsLoading(true);
    try {
      setThreads(await getThreads());
      setError("");
    } catch {
      setError("历史会话读取失败，请检查连接或访问权限。");
    } finally {
      setThreadsLoading(false);
    }
  };
  useEffect(() => {
    let active = true;
    getThreads()
      .then((value) => {
        if (active) setThreads(value);
      })
      .catch(() => {
        if (active) setError("历史会话暂不可用");
      });
    return () => {
      active = false;
    };
  }, [getThreads, setThreads]);
  const loadFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      if (file.size > 10 * 1024 * 1024)
        throw new Error("归档 JSON 超过 10 MB，请使用较小的工具结果文件。");
      const imported = readArchive(JSON.parse(await file.text()));
      if (!imported.results.length && !imported.reports.length)
        throw new Error("文件中没有支持的结果或报告。");
      setArchive(imported);
      setChosen([]);
      setArchiveName(file.name);
      setSelectedId("");
      setEvidence(null);
      setChat(false);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "无法读取归档文件");
    }
  };
  let evidenceValue: unknown;
  try {
    evidenceValue = evidence
      ? pointer(evidence.result.value, evidence.path)
      : null;
  } catch {
    evidenceValue = "字段未载入；不推算缺失结果。";
  }

  return (
    <div className="workbench">
      <aside
        className="wb-nav"
        aria-label="工作台导航"
      >
        <a
          className="wb-brand"
          href="/"
        >
          <Microscope size={25} />
          <span>
            PancAgent<small>科研分析工作台</small>
          </span>
        </a>
        <div className="wb-project">
          <FolderOpen size={15} />
          <span>{projectId}</span>
          <span className="wb-muted">当前项目</span>
        </div>
        <nav>
          {views.map(({ id, label, Icon }) => (
            <button
              key={id}
              title={label}
              aria-label={label}
              aria-current={view === id ? "page" : undefined}
              onClick={() => {
                setView(id);
                setSelectedId("");
                if (evidence) setChat(true);
                setEvidence(null);
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <button
          className="wb-new"
          disabled={stream.isLoading}
          onClick={() => {
            setThreadId(null);
            setArchive(null);
            setChosen([]);
            setSelectedId("");
            setEvidence(null);
            setInput("");
            setResearchScope(null);
            setFollowup(null);
            setChat(true);
          }}
        >
          <SquarePen size={16} /> 新建会话
        </button>
        <div className="wb-nav-foot">
          <span className="wb-dot" />
          {stream.isLoading ? "运行中" : "会话工作区"}
          <a
            href="https://github.com/RAIN-chase/PancAgent"
            target="_blank"
            rel="noreferrer"
            title="PancAgent 仓库"
          >
            <ArrowUpRight size={16} />
          </a>
        </div>
      </aside>
      <div className="wb-shell">
        <header className="wb-top">
          <div>
            <span className="wb-muted">{projectId} / </span>
            <strong>{views.find((item) => item.id === view)?.label}</strong>
          </div>
          <div className="wb-toolbar">
            <button
              className="wb-icon"
              title="新建会话"
              aria-label="新建会话"
              disabled={stream.isLoading}
              onClick={() => {
                setThreadId(null);
                setArchive(null);
                setChosen([]);
                setInput("");
                setResearchScope(null);
                setFollowup(null);
                setSelectedId("");
                setEvidence(null);
                setChat(true);
              }}
            >
              <SquarePen size={17} />
            </button>
            <button
              className="wb-icon"
              title="打开本地归档 JSON（只读）"
              aria-label="打开本地归档 JSON"
              disabled={stream.isLoading}
              onClick={() => fileInput.current?.click()}
            >
              <Upload size={17} />
            </button>
            <input
              ref={fileInput}
              type="file"
              accept=".json,application/json"
              hidden
              onChange={(e) => {
                void loadFile(e.target.files?.[0]);
                e.target.value = "";
              }}
            />
            <button
              className="wb-icon"
              title="科研对话"
              aria-label="科研对话"
              aria-pressed={chat}
              disabled={!!archive}
              onClick={() => {
                setEvidence(null);
                setChat(!chat);
              }}
            >
              <MessageSquare size={18} />
            </button>
          </div>
        </header>
        {archive && (
          <div className="wb-notice">
            本地归档 · {archiveName} · 只读 / 未验证服务器来源
            <button
              className="wb-link"
              onClick={() => {
                setArchive(null);
                setChosen([]);
                setSelectedId("");
                setEvidence(null);
              }}
            >
              返回会话
            </button>
          </div>
        )}
        {error && (
          <div
            className="wb-warning"
            role="alert"
          >
            {error}
            <button
              className="wb-icon"
              title="关闭提示"
              aria-label="关闭提示"
              onClick={() => setError("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
        {!archive && (
          <ResearchScopeBar
            value={activeScope}
            onApply={setResearchScope}
            disabled={stream.isLoading}
          />
        )}
        <div
          className={`wb-body ${chat ? "with-chat" : ""} ${evidence ? "with-evidence" : ""}`}
        >
          <main className="wb-main">
            <div className="wb-heading">
              <div>
                <p className="wb-eyebrow">
                  {view === "protocol"
                    ? "PROTOCOL ANALYSIS"
                    : "RESEARCH WORKSPACE"}
                </p>
                <h1>{views.find((item) => item.id === view)?.label}</h1>
              </div>
              {!archive && (
                <button
                  className="wb-primary"
                  onClick={() => {
                    setEvidence(null);
                    setChat(true);
                  }}
                >
                  <MessageSquare size={16} /> 提出问题
                </button>
              )}
            </div>
            {view === "project" && (
              <>
                <dl className="wb-summary">
                  <div>
                    <dt>Project</dt>
                    <dd>{projectId}</dd>
                  </div>
                  <div>
                    <dt>Thread</dt>
                    <dd>{threadId ?? "尚未创建"}</dd>
                  </div>
                  <div>
                    <dt>已载入结果</dt>
                    <dd>{data.results.length}</dd>
                  </div>
                </dl>
                <section className="wb-band">
                  <h2>Protocols · 当前已载入结果</h2>
                  {catalog.length ? (
                    <div className="wb-table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Protocol ID</th>
                            <th>名称</th>
                            <th>Paper</th>
                            <th>版本</th>
                          </tr>
                        </thead>
                        <tbody>
                          {catalog.map((row) => (
                            <tr key={text(row.protocol_id)}>
                              <td>{text(row.protocol_id)}</td>
                              <td>{displayValue(row.protocol_name)}</td>
                              <td>{displayValue(row.paper_id)}</td>
                              <td>{displayValue(row.version)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (
                    <Empty>当前会话尚无 protocol 目录记录</Empty>
                  )}
                </section>
                <DataCatalog
                  projectId={projectId}
                  readOnly={!!archive}
                  onExplore={(question, dataset) => {
                    if (dataset)
                      setResearchScope({
                        ...activeScope,
                        dataset_ids: [text(dataset.dataset_id)],
                        dataset_versions: [
                          text(dataset.dataset_id) +
                            "=" +
                            text(dataset.version),
                        ],
                      });
                    setEvidence(null);
                    ask(question);
                  }}
                />
                <section className="wb-band">
                  <h2>数据集 · 已载入的 DA 摘要</h2>
                  {datasets.length ? (
                    <div className="wb-table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>Dataset</th>
                            <th>Protocol</th>
                            <th>n_obs</th>
                            <th>n_vars</th>
                            <th>输入版本</th>
                            <th>来源</th>
                          </tr>
                        </thead>
                        <tbody>
                          {datasets.map(({ row, result, index }) => (
                            <tr key={`${result.id}-${index}`}>
                              <td>{displayValue(row.dataset_id)}</td>
                              <td>{displayValue(row.protocol_id)}</td>
                              <td>{displayValue(row.n_obs)}</td>
                              <td>{displayValue(row.n_vars)}</td>
                              <td>{displayValue(result.ref.input_version)}</td>
                              <td>
                                <button
                                  className="wb-icon"
                                  title="检查数据摘要"
                                  aria-label="检查数据摘要"
                                  onClick={() =>
                                    inspect({
                                      result,
                                      path: `/result_preview/dataset_summaries/${index}`,
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
                  ) : (
                    <Empty>尚无已读取的数据集摘要</Empty>
                  )}
                </section>
                <section className="wb-band">
                  <h2>当前会话上下文</h2>
                  <dl className="wb-context">
                    {[
                      "protocol_ids",
                      "endpoints",
                      "protocol_versions",
                      "user_constraints",
                      "pending_questions",
                      "opposing_refs",
                      "result_refs",
                    ].map((key) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd>
                          {archive
                            ? "本地归档不使用会话上下文"
                            : displayValue(session[key])}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p className="wb-muted">会话上下文不是实验事实。</p>
                </section>
                <p className="wb-warning">
                  h5ad
                  保留在服务器。会话结果摘要与服务器数据目录分别保留版本，不自动合并。
                </p>
              </>
            )}
            {["protocol", "phenotype", "hypothesis"].includes(view) && (
              <>
                {view === "protocol" && (
                  <div className="wb-compare-controls">
                    <fieldset className="wb-protocol-picker">
                      <legend>比较对象 · 已选 {chosen.length}</legend>
                      <input
                        aria-label="筛选 Protocol"
                        placeholder="名称或 ID"
                        value={protocolFilter}
                        onChange={(e) => setProtocolFilter(e.target.value)}
                      />
                      <div className="wb-protocol-options">
                        {catalog
                          .filter((row) =>
                            JSON.stringify(row)
                              .toLowerCase()
                              .includes(protocolFilter.toLowerCase()),
                          )
                          .map((row) => {
                            const id = text(row.protocol_id);
                            return (
                              <label key={id}>
                                <input
                                  type="checkbox"
                                  aria-label={`选择 ${id}`}
                                  checked={chosen.includes(id)}
                                  disabled={
                                    !chosen.includes(id) && chosen.length >= 100
                                  }
                                  onChange={(e) =>
                                    setChosen(
                                      e.target.checked
                                        ? [...chosen, id]
                                        : chosen.filter(
                                            (value) => value !== id,
                                          ),
                                    )
                                  }
                                />
                                <span>
                                  {text(row.protocol_name || id)}
                                  <small>{id}</small>
                                </span>
                              </label>
                            );
                          })}
                      </div>
                    </fieldset>
                    <button
                      disabled={chosen.length < 2}
                      onClick={() => {
                        const existing = relevant.find((result) => {
                          const value = comparison(result);
                          const ids = Array.isArray(value.protocol_ids)
                            ? value.protocol_ids
                            : [
                                object(value.protocol_a).protocol_id,
                                object(value.protocol_b).protocol_id,
                              ];
                          return (
                            ids.length === chosen.length &&
                            ids.every((id) => chosen.includes(text(id)))
                          );
                        });
                        if (existing) {
                          setSelectedId(existing.id);
                          setEvidence(null);
                        } else if (!archive)
                          ask(
                            `比较全部 protocols ${JSON.stringify(chosen)} 的特征、完整步骤和来源证据。使用 protocol.compare，protocol_ids=${JSON.stringify(chosen)}。请明确输入版本与缺失信息，不只比较其中两套。`,
                          );
                        else
                          setError(
                            "此归档没有所选对象的比较结果；只读模式不执行新分析。",
                          );
                      }}
                    >
                      查看比较
                    </button>
                    {!archive && (
                      <button
                        disabled={!chosen.length || stream.isLoading}
                        onClick={() =>
                          setResearchScope({
                            ...activeScope,
                            protocol_ids: chosen,
                          })
                        }
                      >
                        应用所选对象到研究范围
                      </button>
                    )}
                  </div>
                )}
                {relevant.length > 0 && (
                  <label className="wb-result-select">
                    已保存结果
                    <select
                      aria-label="已保存结果"
                      value={selected?.id ?? ""}
                      onChange={(e) => {
                        setSelectedId(e.target.value);
                        setEvidence(null);
                      }}
                    >
                      {relevant.map((result) => (
                        <option
                          key={result.id}
                          value={result.id}
                        >
                          {result.action} · {result.runId} · {result.id}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
                {selected && !archive && (
                  <button
                    className="wb-link"
                    onClick={() => askResult({ result: selected, path: "" })}
                  >
                    <MessageSquare size={15} /> 基于此结果追问
                  </button>
                )}
                {!selected ? (
                  <Empty>
                    {view === "protocol"
                      ? "尚无 Protocol 比较结果"
                      : view === "phenotype"
                        ? "尚无已计算的表型结果"
                        : "尚无待验证假说"}
                  </Empty>
                ) : view === "protocol" &&
                  Array.isArray(comparison(selected).protocols) &&
                  comparison(selected).event_step_diff ? (
                  <MultiProtocolResult
                    key={selected.id}
                    result={selected}
                    onInspect={inspect}
                  />
                ) : view === "protocol" && comparison(selected).protocol_a ? (
                  <ProtocolResult
                    key={selected.id}
                    result={selected}
                    onInspect={inspect}
                    onFollowup={
                      archive
                        ? undefined
                        : (question) =>
                            askResult({ result: selected, path: "" }, question)
                    }
                  />
                ) : (
                  <DomainResult
                    key={selected.id}
                    result={selected}
                    onInspect={inspect}
                  />
                )}
              </>
            )}
            {view === "runs" && (
              <>
                {!archive && (
                  <section className="wb-band">
                    <h2>当前运行</h2>
                    <AnalysisProgress values={stream.values} />
                    {!stream.values.plan?.steps?.length && (
                      <p className="wb-muted">无科研分析计划</p>
                    )}
                    <p className="wb-muted">Thread: {threadId ?? "未创建"}</p>
                    <button
                      disabled
                      title="后端取消能力尚未接入"
                    >
                      取消任务 · 不可用
                    </button>
                  </section>
                )}
                <section className="wb-band">
                  <h2>已归档报告</h2>
                  {data.reports.length ? (
                    data.reports.map((report, index) => (
                      <article
                        className="wb-report"
                        key={`${text(report.run_id)}-${index}`}
                      >
                        <div className="wb-toolbar">
                          <code>{text(report.run_id)}</code>
                          <span className="wb-status">
                            {displayValue(report.stop_reason)}
                          </span>
                          <button
                            className="wb-icon"
                            title="下载报告 JSON"
                            aria-label="下载报告 JSON"
                            onClick={() =>
                              downloadJson(
                                report,
                                `report_${text(report.run_id)}.json`,
                              )
                            }
                          >
                            <Download size={16} />
                          </button>
                        </div>
                        <p>{text(report.question)}</p>
                        <p className="wb-muted">
                          问题验收：
                          {displayValue(object(report.acceptance).status)}
                        </p>
                        {rows(report.conclusions).map((claim, i) => (
                          <p key={i}>
                            <strong>{text(claim.evidence_level)}</strong> ·{" "}
                            {text(claim.claim)}
                          </p>
                        ))}
                      </article>
                    ))
                  ) : (
                    <Empty>无已归档科研报告</Empty>
                  )}
                </section>
                {!archive && (
                  <section className="wb-band">
                    <div className="wb-toolbar">
                      <h2>会话历史</h2>
                      <button
                        className="wb-icon"
                        aria-label="刷新会话历史"
                        title="刷新会话历史"
                        disabled={threadsLoading}
                        onClick={() => void refresh()}
                      >
                        <RefreshCw size={16} />
                      </button>
                    </div>
                    {!threads.length && (
                      <p className="wb-muted">尚无可访问的会话</p>
                    )}
                    {threads.map((item) => (
                      <button
                        className="wb-history-row"
                        key={item.thread_id}
                        disabled={stream.isLoading}
                        onClick={() => setThreadId(item.thread_id)}
                      >
                        <History size={15} />
                        <span>
                          {text(object(item.metadata).thread_name) ||
                            text(
                              rows(object(item.values).messages).find(
                                (message) => message.type === "human",
                              )?.content,
                            ) ||
                            item.thread_id}
                        </span>
                        <small>{item.status}</small>
                      </button>
                    ))}
                  </section>
                )}
              </>
            )}
            {latestReport && view !== "runs" && (
              <section className="wb-band">
                <h2>本次问题结论</h2>
                {rows(latestReport.conclusions).map((claim, index) => (
                  <article
                    className="wb-conclusion"
                    key={index}
                  >
                    <small>{text(claim.evidence_level)}</small>
                    <p>{text(claim.claim)}</p>
                    <p className="wb-muted">{text(claim.explanation)}</p>
                    <details>
                      <summary>支持、反对证据与限制</summary>
                      <pre>
                        {JSON.stringify(
                          {
                            supporting_refs: claim.supporting_refs,
                            opposing_refs: claim.opposing_refs,
                            limitations: claim.limitations,
                          },
                          null,
                          2,
                        )}
                      </pre>
                    </details>
                  </article>
                ))}
                {!!boundValues.length && (
                  <details>
                    <summary>
                      定量字段引用 · 展示 {boundValues.length} /{" "}
                      {rows(latestReport.quantitative_bindings).length}
                    </summary>
                    <div className="wb-table-scroll">
                      <table>
                        <thead>
                          <tr>
                            <th>结果 / 字段</th>
                            <th>保存值</th>
                            <th>输入版本</th>
                            <th>来源</th>
                          </tr>
                        </thead>
                        <tbody>
                          {boundValues.map(
                            ({ binding, result, value }, index) => (
                              <tr key={index}>
                                <td>
                                  {text(binding.result_id)}
                                  {text(binding.field_path)}
                                </td>
                                <td>{displayValue(value)}</td>
                                <td>{displayValue(binding.input_version)}</td>
                                <td>
                                  {result && (
                                    <button
                                      className="wb-icon"
                                      title="检查定量字段"
                                      aria-label="检查定量字段"
                                      onClick={() =>
                                        inspect({
                                          result,
                                          path: text(binding.field_path),
                                        })
                                      }
                                    >
                                      <ArrowUpRight size={16} />
                                    </button>
                                  )}
                                </td>
                              </tr>
                            ),
                          )}
                        </tbody>
                      </table>
                    </div>
                  </details>
                )}
              </section>
            )}
          </main>
          {evidence && (
            <aside
              className="wb-evidence"
              aria-label="来源证据"
            >
              <header>
                <strong>来源证据</strong>
                <button
                  className="wb-icon"
                  title="关闭证据"
                  aria-label="关闭证据"
                  onClick={() => {
                    setEvidence(null);
                    if (!archive) setChat(true);
                  }}
                >
                  <X size={17} />
                </button>
              </header>
              {!archive && (
                <button
                  className="wb-link"
                  onClick={() => askResult(evidence)}
                >
                  <MessageSquare size={15} /> 基于此字段追问
                </button>
              )}
              <dl className="wb-context">
                {Object.entries({
                  result_id: evidence.result.id,
                  run_id: evidence.result.runId,
                  field_path: evidence.path,
                  input_version: evidence.result.ref.input_version,
                  sha256: evidence.result.ref.sha256,
                  tool_version: evidence.result.ref.tool_version,
                  parameters: evidence.result.ref.parameters,
                }).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{displayValue(value)}</dd>
                  </div>
                ))}
              </dl>
              <h3>保存字段</h3>
              <pre>{JSON.stringify(evidenceValue, null, 2)}</pre>
              <h3>结果级来源引用</h3>
              <pre>
                {JSON.stringify(
                  evidence.result.value.evidence_refs ?? [],
                  null,
                  2,
                )}
              </pre>
              <h3>限制</h3>
              <pre>
                {JSON.stringify(
                  evidence.result.value.confidence_caveats ?? [],
                  null,
                  2,
                )}
              </pre>
            </aside>
          )}
          {chat && !archive && (
            <div className="wb-chat-drawer">
              <div className="wb-conversation-id">
                研究会话 · {threadId ?? "新会话"}
              </div>
              <button
                className="wb-chat-close wb-icon"
                title="收起对话"
                aria-label="收起对话"
                onClick={() => setChat(false)}
              >
                <X size={17} />
              </button>
              <Thread
                input={input}
                setInput={setInput}
                request={submissionRequest}
                onSubmitted={() => {
                  setFollowup(null);
                  setResearchScope(null);
                }}
              />
              {followup && (
                <div
                  className="wb-followup"
                  role="status"
                >
                  <span>
                    追问引用：{followup.result.id}
                    {followup.path || "（完整结果）"} ·{" "}
                    {displayValue(followup.result.ref.input_version)}
                  </span>
                  <button
                    className="wb-icon"
                    title="移除追问引用"
                    aria-label="移除追问引用"
                    onClick={() => setFollowup(null)}
                  >
                    <X size={15} />
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
