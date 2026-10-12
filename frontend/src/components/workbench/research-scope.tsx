import { useState } from "react";
import { Check, Pencil, RotateCcw } from "lucide-react";
import { scopeFields, type ResearchScope } from "@/lib/workbench";

export function ResearchScopeBar({
  value,
  onApply,
  disabled,
}: {
  value: ResearchScope;
  onApply: (value: ResearchScope) => void;
  disabled: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<Record<string, string>>({});
  return (
    <section
      className="wb-scope"
      aria-label="研究范围"
    >
      <div className="wb-toolbar">
        <strong>研究范围</strong>
        <span className="wb-muted">
          {scopeFields
            .filter(([key]) => value[key]?.length)
            .map(([key, label]) => `${label}: ${value[key].join("、")}`)
            .join(" · ") || "尚未指定"}
        </span>
        <button
          className="wb-icon"
          title="编辑研究范围"
          aria-label="编辑研究范围"
          disabled={disabled}
          onClick={() => {
            setDraft(
              Object.fromEntries(
                scopeFields.map(([key]) => [
                  key,
                  (value[key] ?? []).join("\n"),
                ]),
              ),
            );
            setEditing(!editing);
          }}
        >
          <Pencil size={15} />
        </button>
      </div>
      {editing && (
        <form
          className="wb-scope-editor"
          onSubmit={(event) => {
            event.preventDefault();
            onApply(
              Object.fromEntries(
                scopeFields.map(([key]) => [
                  key,
                  [
                    ...new Set(
                      (draft[key] ?? "")
                        .split("\n")
                        .map((item) => item.trim())
                        .filter(Boolean),
                    ),
                  ],
                ]),
              ),
            );
            setEditing(false);
          }}
        >
          {scopeFields.map(([key, label]) => (
            <label key={key}>
              {label}
              <textarea
                aria-label={label}
                placeholder="每行一项"
                rows={2}
                value={draft[key] ?? ""}
                maxLength={400000}
                onChange={(event) => {
                  const lines = event.target.value
                    .split("\n")
                    .map((item) => item.trim())
                    .filter(Boolean);
                  event.target.setCustomValidity(
                    lines.length > 100 ||
                      lines.some((item) => item.length > 4000)
                      ? "最多 100 项，每项不超过 4000 字符"
                      : "",
                  );
                  setDraft({ ...draft, [key]: event.target.value });
                }}
              />
            </label>
          ))}
          <div className="wb-toolbar">
            <button
              type="submit"
              disabled={disabled}
            >
              <Check size={15} /> 应用范围
            </button>
            <button
              type="button"
              disabled={disabled}
              onClick={() => {
                onApply({});
                setEditing(false);
              }}
            >
              <RotateCcw size={15} /> 清空范围
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
