# Shared Research Session

Protocol comparison, phenotype analysis and hypotheses are views of one Orchestrator thread, not separate agent conversations. The global conversation panel and question draft survive view changes. New conversation and history selection change thread scope; project/thread changes discard local draft selections and imported archives.

## Analysis Scope

The research scope bar contains protocol IDs and versions, dataset IDs and versions, endpoints, cell types, stages and user constraints. Editing uses one item per line. Version entries explicitly identify their object (`ID=version`). Apply updates the local scope for the next submitted question; it does not launch tools or write scientific facts. Clearing the scope does not delete messages or archived evidence.

Protocol picker selection is presentation state until the user applies it to research scope. Exploring a registered dataset explicitly selects its ID and version and prepares a question without submitting it. Switching result views never changes scope or runs an analysis.

At submission, supported scientific summary fields use the existing `request.memory_update` contract. Additional selected scope uses bounded `request.workbench_context` lists. The server validates allowed fields, archives `workbench_context.json`, retains context in the checkpoint and supplies it to Plan/Review. The authenticated server still controls identity, project authorization and dataset path resolution. Context is not an authorization grant or a protocol-to-sample mapping.

## Follow-Up References

Results and the evidence panel expose follow-up actions. These prepare the shared question draft and attach result ID, run ID, field path, input version and hash as structured context. The reference remains visible and removable before submission. Reference pointers are not facts: unavailable content must be clarified, not inferred. Quantitative conclusions continue using the existing saved-result validation.

Conversation and evidence panels occupy the same right-hand area at different times; narrow screens retain a collapsible panel. Imported JSON archives remain read-only and cannot be submitted as server evidence.

## Verification

Browser checks cover desktop/mobile view switching with draft and dataset version preservation, multiline scope editing, structured submission through the existing SDK, field-bound follow-up, no automatic runs, reconnection without resubmission and new-thread clearing. Backend memory tests cover bounded context, archival and checkpoint restoration. These tests mock model/transport responses and do not validate scientific performance against live h5ad datasets.
