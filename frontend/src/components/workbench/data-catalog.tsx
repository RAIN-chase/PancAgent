"use client";

import { useEffect, useState } from "react";
import { ArrowUpRight, RefreshCw } from "lucide-react";
import { getApiKey } from "@/lib/api-key";
import {
  object,
  rows,
  text,
  displayValue,
  type JsonObject,
} from "@/lib/workbench";

const groups = [
  ["reference_atlas", "Reference Atlas"],
  ["public_organoid", "公共类器官数据"],
  ["user_scrna", "我的单细胞转录组"],
];
const statuses: Record<string, string> = {
  pending: "待注册",
  transferring: "传输中",
  ready: "文件就绪",
  unavailable: "不可用",
  failed: "传输失败",
};

export function DataCatalog({
  projectId,
  readOnly,
  onExplore,
}: {
  projectId: string;
  readOnly: boolean;
  onExplore: (question: string, dataset?: JsonObject) => void;
}) {
  const [catalog, setCatalog] = useState<JsonObject>({});
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (readOnly) return;
    const controller = new AbortController();
    const key = getApiKey();
    fetch(`/api/data-catalog?project_id=${encodeURIComponent(projectId)}`, {
      headers: key ? { Authorization: `Bearer ${key}` } : {},
      signal: controller.signal,
      cache: "no-store",
    })
      .then(async (response) => {
        if (!response.ok) throw new Error("数据目录不可用或无访问权限");
        setCatalog(object(await response.json()));
        setError("");
      })
      .catch((err) => {
        if (!controller.signal.aborted) {
          setCatalog({});
          setError(String(err.message));
        }
      });
    return () => controller.abort();
  }, [projectId, readOnly, revision]);
  return (
    <section className="wb-band">
      <div className="wb-toolbar">
        <h2>h5ad 数据目录</h2>
        <button
          className="wb-icon"
          title="刷新数据目录"
          aria-label="刷新数据目录"
          disabled={readOnly}
          onClick={() => setRevision(revision + 1)}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
      {readOnly ? (
        <p className="wb-muted">只读归档不读取当前服务器目录。</p>
      ) : (
        <>
          {!catalog.configured && (
            <p className="wb-muted">服务器数据目录尚未配置</p>
          )}
          {groups.map(([role, label]) => {
            const datasets = rows(catalog.datasets).filter(
              (row) => row.role === role,
            );
            return (
              <section
                className="wb-dataset-group"
                key={role}
              >
                <h3>
                  {label} <small>{datasets.length} 个已登记数据集</small>
                </h3>
                {rows(catalog.collections)
                  .filter((row) => row.role === role)
                  .map((row) => (
                    <p
                      key={text(row.collection_id)}
                      className="wb-muted"
                    >
                      {text(row.name)} ·{" "}
                      {statuses[text(row.status)] ?? displayValue(row.status)}
                      {row.expected_count != null &&
                        ` · 计划 ${displayValue(row.expected_count)} 套（不代表已到齐）`}
                    </p>
                  ))}
                {datasets.length ? (
                  <div className="wb-table-scroll">
                    <table>
                      <thead>
                        <tr>
                          {[
                            "数据集",
                            "Protocol",
                            "状态",
                            "细胞 / 基因",
                            "版本",
                            "来源",
                            "分析",
                          ].map((label) => (
                            <th key={label}>{label}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {datasets.map((row) => (
                          <tr key={text(row.dataset_id)}>
                            <td>
                              {text(row.name || row.dataset_id)}
                              <small>{text(row.dataset_id)}</small>
                              <details>
                                <summary>元信息</summary>
                                <dl>
                                  {[
                                    "description",
                                    "species",
                                    "tissue",
                                    "study_id",
                                    "size_bytes",
                                    "cell_type_column",
                                    "sample_column",
                                    "protocol_column",
                                    "obs_columns",
                                    "layers",
                                  ].map((key) => (
                                    <div key={key}>
                                      <dt>{key}</dt>
                                      <dd>{displayValue(row[key])}</dd>
                                    </div>
                                  ))}
                                </dl>
                              </details>
                            </td>
                            <td>{displayValue(row.protocol_id)}</td>
                            <td>
                              {statuses[text(row.status)] ??
                                displayValue(row.status)}
                              <small>{text(row.availability_note)}</small>
                            </td>
                            <td>
                              {displayValue(row.n_obs)} /{" "}
                              {displayValue(row.n_vars)}
                            </td>
                            <td>{displayValue(row.version)}</td>
                            <td>
                              {/^https?:\/\//.test(text(row.source_url)) ? (
                                <a
                                  href={text(row.source_url)}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  文献 / 数据来源
                                </a>
                              ) : (
                                "未提供"
                              )}
                            </td>
                            <td>
                              <button
                                className="wb-icon"
                                title="探索此数据集"
                                aria-label={`探索 ${text(row.dataset_id)}`}
                                disabled={!row.analysis_available}
                                onClick={() =>
                                  onExplore(
                                    `探索已登记数据集 ${text(row.dataset_id)}（版本 ${text(row.version)}）。先执行 data.assess，request.dataset_id=${JSON.stringify(row.dataset_id)}，request.dataset_version=${JSON.stringify(row.version)}；检查注释与样本结构，不默认运行成熟度或差异分析。`,
                                    row,
                                  )
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
                  <p className="wb-muted">暂无已登记数据</p>
                )}
              </section>
            );
          })}
          <p className="wb-muted">
            用户数据可选；公共数据可直接探索。大型文件通过服务器传输并登记，当前页面不提供浏览器
            h5ad 上传。
          </p>
        </>
      )}
    </section>
  );
}
