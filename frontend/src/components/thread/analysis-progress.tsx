import { Check, Circle, Download, TriangleAlert } from "lucide-react";
import type { AnalysisReport, StateType } from "@/providers/Stream";

export function AnalysisProgress({ values }: { values: StateType }) {
  const plan = values.plan;
  if (!plan?.steps?.length && !values.stop_reason) return null;
  const results = values.step_results ?? [];
  const lastMessage = values.messages.at(-1);
  const candidate =
    lastMessage?.type === "ai"
      ? lastMessage.additional_kwargs?.analysis_report
      : undefined;
  const report =
    candidate && typeof candidate === "object" && "schema_version" in candidate
      ? (candidate as AnalysisReport)
      : null;
  const completed = results.filter((step) => step.usable).length;
  const steps = plan?.steps ?? [];
  const download = () => {
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(report, null, 2)], {
        type: "application/json",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `analysis_${report?.run_id ?? "report"}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <section
      aria-label="Analysis progress"
      className="border-border w-full min-w-0 border-y py-3 text-sm"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-medium">Analysis</h2>
        <span className="text-muted-foreground">
          {completed}/{steps.length}
        </span>
        {report && (
          <button
            type="button"
            onClick={download}
            title="Download saved report JSON"
            aria-label="Download saved report JSON"
            className="hover:bg-muted p-2"
          >
            <Download className="size-4" />
          </button>
        )}
      </div>
      {values.budget && (
        <p className="text-muted-foreground mt-1">
          Tools: {results.length}/{values.budget.max_tool_calls}
        </p>
      )}
      {steps.length > 0 && (
        <progress
          aria-label="Completed analysis steps"
          value={completed}
          max={steps.length}
          className="my-2 h-1.5 w-full accent-emerald-600"
        />
      )}
      <ol className="space-y-2">
        {steps.map((step) => {
          const result = results.find((item) => item.step_id === step.id);
          const Icon = result?.usable ? Check : result ? TriangleAlert : Circle;
          return (
            <li
              key={step.id}
              className="flex min-w-0 items-start gap-2"
            >
              <Icon
                aria-hidden
                className={`mt-0.5 size-4 shrink-0 ${result?.usable ? "text-emerald-600" : result ? "text-amber-600" : "text-muted-foreground"}`}
              />
              <div className="min-w-0 flex-1">
                <span className="font-mono break-all">{step.action_id}</span>
                <span className="text-muted-foreground ml-2 break-all">
                  {result?.status ??
                    (values.stop_reason ? "not_executed" : "pending")}
                </span>
              </div>
            </li>
          );
        })}
      </ol>
      {values.stop_reason && (
        <p
          role="status"
          className="mt-3 font-medium break-all"
        >
          Stop: {values.stop_reason}
        </p>
      )}
      {!!plan?.evidence_gaps?.length && (
        <details className="mt-3">
          <summary className="cursor-pointer">
            Evidence gaps ({plan.evidence_gaps.length})
          </summary>
          <ul className="text-muted-foreground mt-2 space-y-1">
            {plan.evidence_gaps.map((gap, index) => (
              <li
                key={index}
                className="break-words"
              >
                {gap}
              </li>
            ))}
          </ul>
        </details>
      )}
      {!!report?.quantitative_bindings.length && (
        <details className="mt-3">
          <summary className="cursor-pointer">
            Bound results ({report.quantitative_bindings.length})
          </summary>
          <div className="mt-2 max-h-80 overflow-auto">
            <table className="w-full table-fixed text-left text-xs">
              <thead>
                <tr>
                  <th className="pb-2">Result / field</th>
                </tr>
              </thead>
              <tbody>
                {report.quantitative_bindings.map((binding, index) => (
                  <tr
                    key={index}
                    className="border-border border-t"
                  >
                    <td className="py-2 pr-2 font-mono break-all">
                      {binding.result_id}
                      {binding.field_path}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </section>
  );
}
