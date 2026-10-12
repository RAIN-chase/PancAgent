// Synthetic contract fixture, never loaded by the application.
const ref = {
  result_id: "fixture-result",
  input_version: "fixture-input-v1",
  sha256: "fixture-sha",
  parameters: { max_features: 40 },
  tool_version: "1.0.0",
};
const event = {
  stage_id: "S1",
  day_start: 0,
  day_end: 3,
  standardized_name: "Factor X",
  concentration_value: 10,
  concentration_unit: "ng/mL",
  evidence_quote: "Fixture source statement only.",
};
const envelope = {
  schema_version: "orchestrator-evidence-envelope-v2",
  run_id: "fixture-run",
  selected_operation: "protocol.compare",
  status: "ok",
  result_refs: [ref],
  evidence_refs: [
    {
      protocol_id: "fixture-A",
      source_doc_id: "fixture-paper",
      evidence_quote: event.evidence_quote,
    },
  ],
  confidence_caveats: ["Synthetic test data, not a scientific recommendation."],
  result_preview: {
    status: "ok",
    tool_result: {
      protocol_a: {
        protocol_id: "fixture-A",
        metadata: {
          protocol_name: "Protocol Alpha",
          paper_id: "fixture-paper",
        },
      },
      protocol_b: {
        protocol_id: "fixture-B",
        metadata: { protocol_name: "Protocol Beta", paper_id: "fixture-paper" },
      },
      feature_differences: [
        {
          feature: "factor_duration",
          protocol_a_value: 0,
          protocol_b_value: "3.000",
          difference_type: "numeric",
          importance_group: "timing",
        },
        {
          feature: "unknown_dose",
          protocol_a_value: null,
          protocol_b_value: "unparseable",
          difference_type: "missing",
          importance_group: "dose",
        },
      ],
      event_step_diff: {
        protocol_a_events: [event],
        protocol_b_events: [{ ...event, day_end: 6 }],
        protocol_a_steps: [{ raw_text: event.evidence_quote }],
        protocol_b_steps: [],
        semantic_alignment: { unresolved: ["Missing raw B steps"] },
        detail_status: "source_detail_incomplete",
      },
    },
  },
};
const report = {
  schema_version: "scientific-analysis-report-v3",
  run_id: "fixture-run",
  stop_reason: "completed",
  question: "Fixture protocol comparison",
  acceptance: { status: "answered" },
  quantitative_bindings: [],
  conclusions: [
    {
      claim: "Fixture descriptive difference",
      evidence_level: "descriptive",
      explanation: "Fixture only",
      supporting_refs: [],
      opposing_refs: [{ source_id: "fixture-counterevidence" }],
      limitations: ["Not causal"],
    },
  ],
};
report.quantitative_bindings = [
  {
    ...ref,
    field_path:
      "/result_preview/tool_result/feature_differences/0/protocol_b_value",
  },
];
report.display_quantitative_bindings = report.quantitative_bindings;
module.exports = {
  envelope,
  report,
  messages: [
    { type: "tool", content: JSON.stringify(envelope) },
    {
      type: "ai",
      content: "Fixture only",
      additional_kwargs: { analysis_report: report },
    },
  ],
};
