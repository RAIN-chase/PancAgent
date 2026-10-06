from __future__ import annotations

from pathlib import Path
from typing import Any

from protocol_optimization_agent.analysis.protocol.query_engine import ProtocolQueryEngine
from protocol_optimization_agent.runtime.llm_client import LLMClient, OfflineLLMClient


class ProtocolChatAgent:
    """LLM-facing query agent over generated Protocol Analysis outputs.

    The LLM plans tool usage and writes an answer, but all retrieval, matrix
    values, comparisons, and evidence come from ProtocolQueryEngine.
    """

    def __init__(self, output_dir: Path | str, llm_client: LLMClient | None = None) -> None:
        self.engine = ProtocolQueryEngine(Path(output_dir))
        self.llm_client = llm_client or OfflineLLMClient()

    def answer(self, question: str, max_features: int = 30) -> dict[str, Any]:
        plan = self.llm_client.complete_json(
            task="protocol_chat_plan",
            schema_name="ProtocolChatPlan",
            payload={
                "question": question,
                "available_tools": [
                    "find_protocols(query)",
                    "compare_protocols(protocol_id_a, protocol_id_b)",
                    "get_protocol_events(protocol_id)",
                    "get_feature_evidence(protocol_id, feature)",
                    "query_matrix(filters, columns)",
                    "explain_cell(protocol_id, feature)",
                    "propose_feature_specs(question_or_protocol_ids)",
                ],
                "instruction": "Return a JSON plan. Do not answer from memory.",
            },
        )
        if not plan:
            return self.engine.answer_question(question, max_features=max_features)

        tool_result = self._execute_plan(plan, max_features=max_features)
        response = self.llm_client.complete_json(
            task="protocol_chat_answer",
            schema_name="ProtocolChatAnswer",
            payload={
                "question": question,
                "plan": plan,
                "tool_result": tool_result,
                "instruction": (
                    "Answer only from tool_result. Include used features, evidence/provenance availability, "
                    "and confidence caveats. Do not add unsupported biological claims."
                ),
            },
        )
        if not response:
            return {
                "status": "ok",
                "intent": plan.get("intent", "tool_query"),
                "answer": "Tool results are available, but no LLM answer was generated.",
                "tool_result": tool_result,
                "plan": plan,
            }
        response.setdefault("status", "ok")
        response.setdefault("plan", plan)
        response.setdefault("tool_result", tool_result)
        return response

    def _execute_plan(self, plan: dict[str, Any], max_features: int) -> dict[str, Any]:
        intent = str(plan.get("intent") or "")
        if intent == "compare_protocols":
            protocol_ids = [str(item) for item in plan.get("protocol_ids", []) if str(item)]
            queries = [str(item) for item in plan.get("queries", []) if str(item)]
            if len(protocol_ids) < 2 and len(queries) >= 2:
                left = self.engine.find_protocols(queries[0])
                right = self.engine.find_protocols(queries[1])
                if left and right:
                    protocol_ids = [left[0]["protocol_id"], right[0]["protocol_id"]]
            if len(protocol_ids) >= 2:
                return self.engine.compare_protocols(protocol_ids[0], protocol_ids[1], max_features=max_features)
            return {"status": "not_found", "intent": intent, "plan": plan}

        if intent == "find_protocols":
            query = str(plan.get("query") or plan.get("question") or "")
            return {"status": "ok", "results": self.engine.find_protocols(query)}

        if intent == "explain_cell":
            return self.engine.explain_cell(str(plan.get("protocol_id") or ""), str(plan.get("feature") or ""))

        if intent == "get_protocol_events":
            return {"status": "ok", "events": self.engine.get_protocol_events(str(plan.get("protocol_id") or ""))}

        if intent == "query_matrix":
            return {
                "status": "ok",
                "rows": self.engine.query_matrix(
                    filters=plan.get("filters") or {},
                    columns=plan.get("columns") or None,
                    limit=plan.get("limit") or 50,
                ),
            }

        if intent == "propose_feature_specs":
            return self.engine.propose_feature_specs_context(
                protocol_ids=[str(item) for item in plan.get("protocol_ids", []) if str(item)]
            )

        return self.engine.answer_question(str(plan.get("question") or ""), max_features=max_features)
