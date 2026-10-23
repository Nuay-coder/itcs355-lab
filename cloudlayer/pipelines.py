"""Lab 5 — compile pipeline/pipeline.yaml into your provider's pipeline service.

This module is Layer 3, so provider SDKs are allowed here and nowhere else. The DAG stays
in YAML; only the translation lives here. That separation is what you argue for or against
in Lab 5 Task 4.

Implement ONE compile function, for your provider.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

SPEC = Path(__file__).resolve().parents[1] / "pipeline" / "pipeline.yaml"


def load_spec(path: Path = SPEC) -> dict[str, Any]:
    """Read the neutral DAG and expand ${VAR} references from the environment."""
    raw = path.read_text()
    for key, value in os.environ.items():
        raw = raw.replace(f"${{{key}}}", value)
    return yaml.safe_load(raw)


def compile_aws(spec: dict[str, Any]):
    """SageMaker Pipelines.

    Each step becomes a ProcessingStep or TrainingStep; `condition` becomes a
    ConditionStep. Note that SageMaker's condition steps branch rather than abort, so
    `on_failure: abort` needs a FailStep on the else branch — an asymmetry worth
    mentioning in your Lab 5 write-up.
    """
    raise NotImplementedError("TODO Lab 5: build a sagemaker.workflow.pipeline.Pipeline")


def compile_azure(spec: dict[str, Any]):
    """Azure ML Pipelines.

    Each step becomes a command component; the DAG is expressed as a @pipeline function.
    Conditions use azure.ai.ml.dsl.condition, which is closer to this YAML than the other
    two providers' equivalents.
    """
    raise NotImplementedError("TODO Lab 5: build an azure.ai.ml pipeline job")


def _parse_condition(expr: str) -> tuple[str, str]:
    """`"gate_decision == 'pass'"` -> ("gate_decision", "pass")."""
    field, _, value = expr.partition("==")
    return field.strip(), value.strip().strip("'\"")


def _build_component(step: dict[str, Any]):
    """One KFP container component per DAG step. A step that declares `gate_decision`
    as an output gets an extra `--gate-decision-out <path>` argument wired to a real
    KFP output, so `register`'s condition can branch on it instead of being decorative."""
    from kfp import dsl

    image, command = step["image"], list(step["command"])
    emits_gate_decision = "gate_decision" in step.get("outputs", [])

    if emits_gate_decision:
        def _component(gate_decision):
            return dsl.ContainerSpec(
                image=image, command=command + ["--gate-decision-out", gate_decision],
            )
        # This module uses `from __future__ import annotations`, which stores the
        # annotation below as the plain string "dsl.OutputPath(str)" rather than the
        # real object — kfp's component_factory then fails trying to interpret that
        # string as a bundled artifact type. Overwrite it with the real object before
        # the decorator inspects the signature.
        _component.__annotations__["gate_decision"] = dsl.OutputPath(str)
    else:
        def _component():
            return dsl.ContainerSpec(image=image, command=command)

    return dsl.container_component(_component)


def compile_gcp(spec: dict[str, Any]):
    """Vertex AI Pipelines.

    Kubeflow Pipelines underneath: steps become container components, `depends_on`
    becomes `.after()`, and `condition` becomes a real `dsl.If` keyed on the upstream
    step's own output — not a static flag — so a candidate that fails the gate
    genuinely does not reach `register`. The compiled artifact is a JSON file you
    submit, so this function produces a file rather than an object, unlike the other
    two providers.
    """
    from kfp import dsl
    from kfp.compiler import Compiler

    steps: list[dict[str, Any]] = spec["steps"]
    by_name = {s["name"]: s for s in steps}
    # output name -> name of the step that produces it, for condition wiring.
    producer_of: dict[str, str] = {
        out: s["name"] for s in steps for out in s.get("outputs", [])
    }

    # Group contiguous steps that share one gate (a step with `condition`, plus every
    # step transitively depending on it) so they open exactly ONE dsl.If block. KFP
    # rejects a downstream task depending on an upstream task from a *different*
    # dsl.If instance, even when both conditions are identical in substance.
    groups: list[tuple[str | None, list[dict[str, Any]]]] = []
    gated_step: str | None = None
    for step in steps:
        condition = step.get("condition")
        depends_on_gated = gated_step is not None and gated_step in step.get("depends_on", [])
        if condition or depends_on_gated:
            gating_step = step["name"] if condition else gated_step
            if groups and groups[-1][0] == gating_step:
                groups[-1][1].append(step)
            else:
                groups.append((gating_step, [step]))
            gated_step = step["name"]
        else:
            groups.append((None, [step]))
            gated_step = None

    @dsl.pipeline(name=spec["name"])
    def _pipeline():
        tasks: dict[str, Any] = {}

        def emit(step: dict[str, Any]) -> None:
            task = _build_component(step)()
            task.set_display_name(step["name"])
            for dep in step.get("depends_on", []):
                task.after(tasks[dep])
            tasks[step["name"]] = task

        for gating_step, group_steps in groups:
            if gating_step is None:
                for step in group_steps:
                    emit(step)
                continue
            field, expected = _parse_condition(by_name[gating_step]["condition"])
            gate_task = tasks[producer_of[field]]
            with dsl.If(gate_task.outputs[field] == expected, name=f"{gating_step}-gated"):
                for step in group_steps:
                    emit(step)

    out_path = Path(__file__).resolve().parents[1] / "pipeline" / "compiled" / "gcp_pipeline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    Compiler().compile(pipeline_func=_pipeline, package_path=str(out_path))
    return out_path


COMPILERS = {"aws": compile_aws, "azure": compile_azure, "gcp": compile_gcp}


def compile_for(provider: str, spec: dict[str, Any] | None = None):
    spec = spec or load_spec()
    try:
        return COMPILERS[provider.lower()](spec)
    except KeyError:
        raise ValueError(f"No pipeline compiler for {provider!r}") from None
