"""Render the code-grounded framework figure; never invoke project workflows."""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch
from matplotlib.path import Path as MplPath


ROOT = Path(__file__).resolve().parent
matplotlib.rcParams.update({"font.family": "Arial", "svg.fonttype": "none", "pdf.fonttype": 42})
fig, ax = plt.subplots(figsize=(19.2, 11.2), dpi=120)
fig.patch.set_facecolor("white")
ax.set(xlim=(0, 1920), ylim=(1120, 0))
ax.axis("off")
fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

INK = "#192A38"
MUTED = "#52616F"
BLUE = "#245CAF"
TEAL = "#078B91"
GREEN = "#49853F"
CORAL = "#BD5145"
VIOLET = "#76549B"
GREY = "#6F7D89"


def text(x, y, value, size=12, color=INK, weight="normal", ha="left"):
    ax.text(x, y, value, fontsize=size, color=color, fontweight=weight, ha=ha,
            va="top", linespacing=1.38, zorder=5)


def box(x, y, w, h, edge, fill="white", radius=9, lw=1.4, dashed=False):
    patch = FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={radius}",
                          edgecolor=edge, facecolor=fill, linewidth=lw,
                          linestyle=(0, (5, 4)) if dashed else "solid", zorder=1)
    ax.add_patch(patch)


def path(points, color=GREY, lw=1.8, dashed=False, double=False):
    codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(points) - 1)
    arrow = FancyArrowPatch(path=MplPath(points, codes), arrowstyle="<->" if double else "-|>",
                            mutation_scale=14, linewidth=lw, color=color,
                            linestyle=(0, (5, 4)) if dashed else "solid", zorder=3)
    ax.add_patch(arrow)


def badge(x, y, label="LLM", color=BLUE, w=43):
    box(x, y, w, 22, color, "#EEF4FD", radius=4, lw=0.8)
    text(x + w / 2, y + 4, label, size=8, color=color, weight="bold", ha="center")


def step(x, y, w, title, detail, color, llm=False, h=77):
    box(x, y, w, h, color, radius=5, lw=0.9)
    text(x + 14, y + 10, title, size=12, weight="bold")
    text(x + 14, y + 33, detail, size=9.5, color=MUTED)
    if llm:
        badge(x + w - 55, y + 9)


text(32, 23, "Evidence-Grounded Protocol Research Agent", size=27, weight="bold")
text(34, 70, "Pancreatic / islet organoids: protocol assessment, phenotype analysis and testable optimization hypotheses", size=13, color=MUTED)

# Five columns are conceptual responsibilities, not five independent agents.
box(32, 152, 240, 600, GREY, "#F5F7F8", lw=1.8)
text(49, 170, "A  Research Inputs", size=15, weight="bold")
items = [
    (215, "Research question", "Objects, endpoints, constraints"),
    (307, "Structured protocols", "Upstream-exported JSONL\nSource quotes and identifiers"),
    (410, "AnnData / h5ad", "Expression + annotations\nSample / study / protocol links"),
    (513, "Mechanism literature", "Provided sources\nOptional PubMed abstracts"),
]
for y, title, detail in items:
    box(48, y, 208, 83, "#CFD8DE", radius=5, lw=0.8)
    text(61, y + 12, title, size=12, weight="bold")
    text(61, y + 39, detail, size=10, color=MUTED)
text(49, 628, "Available execution modes", size=11, weight="bold")
text(49, 656, "Chat: question-driven actions\nBatch: PA > DA > PO", size=10, color=MUTED)
text(49, 711, "Extraction is upstream, not integrated.", size=8, color=MUTED)

box(306, 152, 350, 600, BLUE, "#F1F6FD", lw=1.8)
text(323, 170, "B  Orchestrator", size=15, weight="bold")
text(323, 204, "Single bounded Plan / Execute / Review loop", size=10, color=BLUE)
for y, title, detail, llm in [
    (238, "Configure", "Identity, project scope, run snapshots", False),
    (330, "Plan", "Goal, objects, metrics, result bindings", True),
    (422, "Execute", "Dispatch available registered actions", False),
    (514, "Review", "Evidence grounding + goal acceptance", True),
    (606, "Finalize", "Source-bound conclusions + sealed report", False),
]:
    step(323, y, 313, title, detail, BLUE, llm, h=74)
for y in (312, 404, 496, 588):
    path([(469, y), (469, y + 18)], BLUE, lw=1.3)
# The right-side feedback route is separate from tool-result return.
path([(626, 552), (645, 552), (645, 459), (626, 459)], BLUE, lw=1.3)
text(324, 700, "Action Registry: availability / inputs / outputs", size=9, color=BLUE, weight="bold")
text(324, 724, "Bounded repair; local failures; clarification", size=9, color=MUTED)

box(708, 152, 450, 284, TEAL, "#F0FAFA", lw=1.8)
text(725, 170, "C1  Protocol Analysis Agent (PA)", size=15, weight="bold")
text(725, 203, "Source-grounded representation and comparison", size=10, color=TEAL)
step(725, 233, 416, "Protocol Structure & Step Semantics", "Schema, timeline, factor events;\nscoped operations and parameters", TEAL, h=68)
badge(1043, 272, "Optional LLM", TEAL, w=88)
step(725, 316, 416, "Deterministic Feature Compiler", "Feature specs, numeric values, provenance, audit", TEAL, h=68)
text(725, 400, "Outputs: matrix + original steps + source evidence", size=10, color=TEAL, weight="bold")

box(708, 467, 450, 285, GREEN, "#F4F9F1", lw=1.8)
text(725, 485, "C2  Data Analysis Agent (DA)", size=15, weight="bold")
text(725, 518, "Explicit tasks; deterministic tools", size=10, color=GREEN)
badge(1043, 514, "Optional LLM", GREEN, w=88)
step(725, 548, 416, "Task Resolver & Phenotype Tools", "QC; composition, expression, markers;\ngene programs and differential expression", GREEN, h=68)
step(725, 631, 416, "Maturity & Metric Validation", "Early / Late scores; task-specific validity;\nstatistical applicability", GREEN, h=68)
text(725, 715, "Outputs: Phenotype IR + numeric tables + validation", size=10, color=GREEN, weight="bold")

box(1210, 152, 350, 600, CORAL, "#FFF5F3", lw=1.8)
text(1227, 170, "D  Protocol Optimizing", size=15, weight="bold")
text(1227, 200, "Agent (PO)", size=15, weight="bold")
text(1227, 233, "Evidence-to-hypothesis synthesis", size=10, color=CORAL)
po_steps = [
    (266, "Joint Evidence Builder", "Protocol identity / version + endpoint cohorts"),
    (349, "Association & Sensitivity", "Raw endpoints; replicates; study removal"),
    (432, "Mechanism Evidence Integrator", "Support / opposition;\nalternative explanations"),
    (515, "Hypothesis & Candidate Design", "Intervention, control, endpoint, falsification"),
    (598, "Critique & Conditional Selection", "Sourced utility; missing assessments\nremain unknown / unranked"),
]
for y, title, detail in po_steps:
    step(1227, y, 316, title, detail, CORAL, h=68)
badge(1441, 470, "Optional LLM", CORAL, w=88)
for y in (334, 417, 500, 583):
    path([(1385, y), (1385, y + 15)], CORAL, lw=1.3)
text(1227, 687, "Similar observed protocols are references,", size=9, color=MUTED)
text(1227, 709, "not predictions of a new intervention.", size=9, color=MUTED)
text(1227, 733, "Association is not causation.", size=9, color=CORAL, weight="bold")

box(1612, 152, 276, 600, VIOLET, "#F8F5FB", lw=1.8)
text(1629, 170, "E  Research Outputs", size=15, weight="bold")
text(1629, 205, "Finalized by Orchestrator", size=10, color=VIOLET)
for y, title, detail in [
    (241, "Protocol differences", "Features + steps + context"),
    (326, "Phenotype assessment", "Endpoint-specific comparisons"),
    (411, "Scientific explanations", "Support, opposition, limitations"),
    (496, "Testable hypotheses", "Validation-ready specifications"),
    (581, "Traceable artifacts", "Versioned JSON, tables, reports"),
]:
    step(1629, y, 242, title, detail, VIOLET, h=69)
text(1629, 680, "Evidence gaps remain explicit.", size=10, color=VIOLET, weight="bold")
text(1629, 708, "No guaranteed improvement.\nNo automatic wet-lab execution.", size=9, color=MUTED)

# Connections encode responsibility and evidence flow, never implied causation.
path([(256, 257), (306, 257)], BLUE)
text(280, 221, "Question", size=8, color=BLUE, ha="center")
path([(656, 457), (678, 457), (678, 274), (708, 274)], BLUE)
path([(678, 457), (678, 589), (708, 589)], BLUE)
path([(678, 457), (690, 457), (690, 138), (1192, 138), (1192, 258), (1210, 258)], BLUE, lw=1.3)
text(868, 122, "Registered action dispatch", size=8, color=BLUE)
path([(1158, 410), (1183, 410), (1183, 300), (1210, 300)], TEAL)
path([(1158, 726), (1197, 726), (1197, 318), (1210, 318)], GREEN)
path([(1560, 456), (1612, 456)], CORAL)
text(1586, 420, "Report", size=8, color=CORAL, ha="center")
path([(1545, 152), (1545, 118), (645, 118), (645, 531), (635, 531)], BLUE, lw=1.6)
text(1060, 96, "Domain results + evidence references return to Review", size=10, color=BLUE, ha="center")

box(32, 797, 1856, 149, GREY, "#F6F8FA", lw=1.5)
text(52, 813, "Shared Scientific State & Evidence Governance", size=15, weight="bold")
for x, title, detail in [
    (52, "Versioned Scientific Records", "Protocol / Phenotype / Evidence / Claim / Hypothesis\nHistorical counterevidence; user / project isolation"),
    (686, "Result Authority & Reproducibility", "Hash-verified saved JSON + field-level references\nInput, code, tool and model versions; immutable archives"),
    (1308, "Scientific & Execution Gates", "Source coverage / QC / comparability / goal acceptance\nUnknowns preserved; budget / audit / clarification"),
]:
    text(x, 852, title, size=11, weight="bold")
    text(x, 881, detail, size=10, color=MUTED)
for x in (481, 933, 1385):
    path([(x, 752), (x, 797)], GREY, lw=1.3, double=True)
text(504, 770, "Read / persist", size=8, color=MUTED)

text(32, 965, "Optional external validation: supported extension, not an autonomous experimental loop", size=11, color=MUTED)
for x, w, label in [
    (32, 270, "Researcher Review"),
    (375, 430, "External Culture / Experiment / Sequencing"),
    (878, 377, "QC-Passed Feedback Admission"),
]:
    box(x, 999, w, 47, GREY, radius=5, dashed=True, lw=1.0)
    text(x + w / 2, 1012, label, size=10, ha="center")
path([(302, 1023), (375, 1023)], GREY, dashed=True)
path([(805, 1023), (878, 1023)], GREY, dashed=True)
path([(1255, 1023), (1280, 1023), (1280, 946)], GREY, dashed=True)
text(1361, 979, "LLM: planning / qualitative interpretation", size=10, color=BLUE)
text(1361, 1008, "Tools: numerical authority", size=10, color=TEAL)
text(1361, 1037, "Dashed: external, optional validation", size=10, color=MUTED)
text(32, 1080, "Current-code framework | 2026-10-07 | Module names summarize responsibilities; they do not introduce extra agents.", size=9, color=MUTED)

for suffix in ("svg", "pdf", "png"):
    output = ROOT / f"protocol_research_framework_2026-10-07.{suffix}"
    fig.savefig(output, dpi=240, facecolor="white", metadata={"Creator": "Protocol research architecture documentation"})
    print(output)
plt.close(fig)
