"""Ratchet service API — manual trigger only.

No apply endpoint, nothing on a schedule. ``decide`` is a human writing
``{decision, decided_by, why}``; a kept mutation is a git commit made
outside the service (guide 01.4).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.ratchet import golden, registry, store
from services.ratchet.components.formulator import metrics as formulator_metrics

app = FastAPI(title="ratchet-svc", version="0.1.0")

COMPONENT_DIR = Path(__file__).parent / "components"


class RegisterGoldenBody(BaseModel):
    component: str
    path: str


class CreateExperimentBody(BaseModel):
    component: str
    golden_id: str
    baseline_prompt: str
    mutation_prompt: str | None = None
    target_metric: str = "node_accuracy"
    guard_metrics: list[str] = ["standard_accuracy", "clarify_accuracy"]


class DecideBody(BaseModel):
    decision: str
    decided_by: str = Field(min_length=1)
    why: str = Field(min_length=1)


@app.get("/health")
def health():
    return {"status": "ok", "service": "ratchet"}


@app.post("/golden")
def register_golden(body: RegisterGoldenBody):
    """Register a golden file: validate against the component schema, hash-verify, store."""
    comp_dir = COMPONENT_DIR / body.component
    path = comp_dir / body.path
    if not path.is_file():
        raise HTTPException(404, f"no golden file at {path}")
    try:
        result = golden.register(path, body.component)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@app.get("/golden")
def list_golden():
    return {"goldens": store.list_goldens()}


@app.post("/experiments")
def create_experiment(body: CreateExperimentBody):
    """Create an experiment. Component mutex: one in-flight experiment per component."""
    registry.get_component(body.component)

    existing = [
        e for e in store.list_experiments()
        if e["component"] == body.component and not e.get("decision")
    ]
    if existing:
        raise HTTPException(409, f"component '{body.component}' already has in-flight experiment {existing[0]['id']}")

    golden_row = store.get_golden(body.golden_id)
    if not golden_row:
        raise HTTPException(404, f"golden set '{body.golden_id}' not registered")

    for pname in (body.baseline_prompt, body.mutation_prompt):
        if pname and not _prompt_file(pname).is_file():
            raise HTTPException(404, f"prompt file '{pname}' not found under prompts/")

    comp = registry.get_component(body.component)
    menu_sha = _standards_menu_sha()

    eid = f"exp_{uuid4().hex[:8]}"
    store.create_experiment(
        eid=eid,
        component=body.component,
        golden_id=body.golden_id,
        golden_sha=golden_row["sha256"],
        standards_menu_sha=menu_sha,
        baseline_prompt=body.baseline_prompt,
        mutation_prompt=body.mutation_prompt,
        target_metric=body.target_metric,
        guard_metrics=body.guard_metrics,
        system_version=SYSTEM_VERSION,
    )
    return {
        "id": eid,
        "component": body.component,
        "golden_id": body.golden_id,
        "golden_sha": golden_row["sha256"],
        "standards_menu_sha": menu_sha,
        "system_version": SYSTEM_VERSION,
    }


@app.post("/experiments/{eid}/run")
def run_experiment(
    eid: str,
    prompt: str = Query(..., pattern="^(baseline|mutation)$"),
    split: str = Query(..., pattern="^(calibration|heldout)$"),
    item_ids: str | None = Query(None, description="comma-separated item_ids; all when omitted"),
    repeat: int = Query(1, ge=1, le=10),
):
    """Run a prompt over a split. Manual only — every run is an explicit call."""
    exp = store.get_experiment(eid)
    if not exp:
        raise HTTPException(404, f"experiment {eid} not found")
    if exp.get("decision"):
        raise HTTPException(409, f"experiment {eid} already decided ({exp['decision']})")

    prompt_name = exp["baseline_prompt"] if prompt == "baseline" else exp.get("mutation_prompt")
    if prompt == "mutation" and not prompt_name:
        raise HTTPException(400, "experiment has no mutation_prompt")
    template = _prompt_file(prompt_name).read_text()

    comp = registry.get_component(exp["component"])
    golden_items, _ = golden.load_golden(_golden_path(exp), exp["component"])

    split_items = [i for i in golden_items if i.split == split and not i.exclude_from_scoring]
    wanted = items_set(item_ids)
    if wanted is not None:
        split_items = [i for i in split_items if i.item_id in wanted]

    if not split_items:
        raise HTTPException(400, f"no scorable items in split '{split}'")

    for i in split_items:
        for _ in range(repeat):
            actual = comp.replay_fn(i, template)
            hits = comp.metrics.grade_row(i.expected, actual)
            store.insert_run(
                experiment_id=eid,
                prompt_version=prompt_name,
                split=split,
                item_id=i.item_id,
                actual=actual,
                hits=hits,
                raw_response=actual.get("raw_response"),
                duration_ms=actual.get("duration_ms"),
            )

    _maybe_stamp_determinism(eid)
    _recompute_scores(eid)
    return {"experiment_id": eid, "prompt": prompt_name, "split": split,
            "ran": len(split_items), "repeat": repeat}


@app.get("/experiments/{eid}")
def get_experiment(eid: str):
    """Scores + per-row hits/misses. Experiments with different standards_menu_sha
    are flagged incomparable, never silently charted together."""
    exp = store.get_experiment(eid)
    if not exp:
        raise HTTPException(404, f"experiment {eid} not found")

    runs = store.runs_for(eid)
    rows = []
    for r in runs:
        rows.append({
            "prompt": r["prompt_version"],
            "split": r["split"],
            "item_id": r["item_id"],
            "actual": r["actual"],
            "hits": r["hits"],
            "raw_response": r.get("raw_response"),
            "duration_ms": r.get("duration_ms"),
        })

    peers = [e for e in store.list_experiments()
             if e["component"] == exp["component"] and e["id"] != eid
             and e.get("standards_menu_sha") != exp.get("standards_menu_sha")]
    return {
        "experiment": exp,
        "rows": rows,
        "incomparable_with": [
            {"id": p["id"], "standards_menu_sha": p.get("standards_menu_sha")}
            for p in peers
        ],
    }


@app.post("/experiments/{eid}/decide")
def decide(eid: str, body: DecideBody):
    """Human decision. Required: non-empty decided_by and why — three cycles
    from now, 'why did we revert v3?' must not need archaeology."""
    exp = store.get_experiment(eid)
    if not exp:
        raise HTTPException(404, f"experiment {eid} not found")
    if body.decision not in ("keep", "revert"):
        raise HTTPException(400, "decision must be 'keep' or 'revert'")
    store.decide_experiment(eid, body.decision, body.decided_by, body.why)
    return {"id": eid, "decision": body.decision, "decided_by": body.decided_by}


@app.get("/components")
def components():
    return {"components": registry.list_components()}


# ── internals ────────────────────────────────────────────────────

SYSTEM_VERSION = "0.1.0"


def _prompt_file(name: str) -> Path:
    p = COMPONENT_DIR / "formulator" / "prompts" / name
    if p.suffix != ".txt":
        p = p.with_suffix(".txt")
    return p


def _golden_path(exp: dict) -> Path:
    return COMPONENT_DIR / exp["component"] / "golden" / f"{exp['golden_id']}.jsonl"


def _standards_menu_sha() -> str:
    from backend.planning.meta_planner.goal_formulator import build_standards_menu
    menu = build_standards_menu()
    return hashlib.sha256(menu.encode()).hexdigest()


def items_set(items_param: str) -> set[str] | None:
    if not items_param:
        return None
    return {s for s in items_param.split(",") if s}


def _semantic(actual: dict) -> dict:
    """The fields that matter for grading — raw_response and timing are noise."""
    return {k: actual[k] for k in ("standards", "subdirs", "clarify", "estimated_nodes") if k in actual}


def _maybe_stamp_determinism(eid: str) -> None:
    runs = store.runs_for(eid)
    per_row: dict[tuple, list[str]] = {}
    for r in runs:
        key = (r["item_id"], r["prompt_version"])
        per_row.setdefault(key, []).append(json.dumps(_semantic(r["actual"]), sort_keys=True))
    repeats = max(len(v) for v in per_row.values()) if per_row else 1
    if repeats <= 1:
        return
    deterministic = all(len(set(v)) == 1 for v in per_row.values())
    store.set_experiment_determinism(eid, "deterministic" if deterministic else "nondeterministic")


def _recompute_scores(eid: str) -> None:
    """Recompute calibration/heldout scores from stored runs.

    When a row has repeated runs (determinism regime), each hit key is
    majority-voted across the repeats before aggregation — a 1-row
    difference between prompts at n=21 is noise, and the majority keeps
    that honest.
    """
    runs = store.runs_for(eid)
    if not runs:
        return
    scores: dict[str, dict] = {"calibration": {}, "heldout": {}}
    for split in ("calibration", "heldout"):
        subset = [r for r in runs if r["split"] == split]
        if not subset:
            continue
        by_row: dict[tuple[str, str], list[dict]] = {}
        for r in subset:
            by_row.setdefault((r["prompt_version"], r["item_id"]), []).append(r["hits"])
        by_prompt: dict[str, list[dict]] = {}
        for (prompt, _), row_hits in by_row.items():
            by_prompt.setdefault(prompt, []).append(_majority(row_hits))
        scores[split] = {
            name: formulator_metrics.aggregate(hits_list)
            for name, hits_list in by_prompt.items()
        }
    store.set_experiment_scores(eid, scores)


def _majority(row_hits: list[dict]) -> dict:
    """Per-key majority vote across repeats of one row."""
    if not row_hits:
        return {}
    keys = row_hits[0].keys()
    out = {}
    for k in keys:
        votes = [h[k] for h in row_hits]
        out[k] = max(set(votes), key=votes.count)
    return out


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8096)
