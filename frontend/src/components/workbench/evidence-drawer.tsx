import { ArrowUpRight, MessageSquare, X } from "lucide-react";
import { displayValue, pointer, rows } from "@/lib/workbench";
import type { EvidenceSelection } from "./results";

export function EvidenceDrawer({
  evidence,
  onClose,
  onSelect,
  onFollowup,
}: {
  evidence: EvidenceSelection;
  onClose: () => void;
  onSelect: (selection: EvidenceSelection) => void;
  onFollowup?: (selection: EvidenceSelection) => void;
}) {
  let evidenceValue: unknown;
  let fieldAvailable = true;
  try {
    evidenceValue = pointer(evidence.result.value, evidence.path);
  } catch {
    fieldAvailable = false;
    evidenceValue = "字段未载入；不推算缺失结果。";
  }
  return (
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
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </header>
      {onFollowup && fieldAvailable && (
        <button
          className="wb-link"
          onClick={() => onFollowup(evidence)}
        >
          <MessageSquare size={15} /> 基于此字段追问
        </button>
      )}
      {rows(evidence.result.value.execution_artifacts).length > 0 && (
        <button
          className="wb-link"
          onClick={() =>
            onSelect({ ...evidence, path: "/execution_artifacts" })
          }
        >
          检查执行脚本与环境 <ArrowUpRight size={14} />
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
        {JSON.stringify(evidence.result.value.evidence_refs ?? [], null, 2)}
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
  );
}
