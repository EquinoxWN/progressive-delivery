"""The offline checks: the real config passes, and each kind of mistake is caught."""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from pdcheck import checks
from pdcheck.__main__ import main

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config"


@pytest.fixture(scope="session")
def crd() -> Path:
    """Flagger's CRD file, downloaded once into .tmp and checked against its pinned SHA-256."""
    return checks.fetch_crd(ROOT / ".tmp")


def test_the_real_config_passes(crd: Path) -> None:
    assert checks.check_all(CONFIG, crd) == []
    kinds = sorted(d.kind for d in checks.load(CONFIG))
    assert kinds == ["Canary", "Deployment", "Gateway", "GatewayClass", "Namespace", "Namespace"]


def mutated(tmp_path: Path, filename: str, change: Callable[[dict[str, Any]], None]) -> Path:
    """A copy of config/ with one document of one file changed."""
    root = tmp_path / "config"
    shutil.copytree(CONFIG, root)
    folder = "gateway" if filename == "gateway.yaml" else "app"
    p = root / folder / filename
    docs = list(yaml.safe_load_all(p.read_text(encoding="utf-8")))
    target = next(d for d in docs if d["kind"] in ("Deployment", "Canary", "Gateway"))
    change(target)
    p.write_text(yaml.safe_dump_all(docs), encoding="utf-8")
    return root


def container(d: dict[str, Any]) -> dict[str, Any]:
    """The first container of a Deployment."""
    c: dict[str, Any] = d["spec"]["template"]["spec"]["containers"][0]
    return c


WORKLOAD_MUTATIONS: dict[str, Callable[[dict[str, Any]], None]] = {
    "image must be pinned": lambda d: container(d).update(image="ghcr.io/stefanprodan/podinfo:6.14.0"),
    "runAsNonRoot": lambda d: d["spec"]["template"]["spec"]["securityContext"].pop("runAsNonRoot"),
    "seccomp": lambda d: d["spec"]["template"]["spec"]["securityContext"].pop("seccompProfile"),
    "service account token": lambda d: d["spec"]["template"]["spec"].pop("automountServiceAccountToken"),
    "hostNetwork": lambda d: d["spec"]["template"]["spec"].update(hostNetwork=True),
    "hostPath": lambda d: d["spec"]["template"]["spec"]["volumes"].append({"name": "h", "hostPath": {"path": "/"}}),
    "privileged": lambda d: container(d)["securityContext"].update(privileged=True),
    "allowPrivilegeEscalation": lambda d: container(d)["securityContext"].update(allowPrivilegeEscalation=True),
    "read-only": lambda d: container(d)["securityContext"].update(readOnlyRootFilesystem=False),
    "drop ALL": lambda d: container(d)["securityContext"]["capabilities"].update(drop=[]),
    "limits.memory": lambda d: container(d)["resources"]["limits"].pop("memory"),
    "requests.cpu": lambda d: container(d)["resources"]["requests"].pop("cpu"),
    "readinessProbe": lambda d: container(d).pop("readinessProbe"),
    "livenessProbe": lambda d: container(d).pop("livenessProbe"),
}


@pytest.mark.parametrize("expected", list(WORKLOAD_MUTATIONS))
def test_each_workload_mistake_is_caught(crd: Path, tmp_path: Path, expected: str) -> None:
    errs = checks.check_all(mutated(tmp_path, "deployment.yaml", WORKLOAD_MUTATIONS[expected]), crd)
    assert any(expected in e for e in errs), errs


CANARY_MUTATIONS: dict[str, tuple[str, Callable[[dict[str, Any]], None]]] = {
    "schema: interval type": ("canary.yaml", lambda d: d["spec"]["analysis"].update(interval=15)),
    "schema: missing targetRef": ("canary.yaml", lambda d: d["spec"].pop("targetRef")),
    "schema: unknown provider field type": ("canary.yaml", lambda d: d["spec"]["service"].update(port="9898")),
    "targetRef must name": ("canary.yaml", lambda d: d["spec"]["targetRef"].update(name="missing")),
    "targetPort is not a container port": ("canary.yaml", lambda d: d["spec"]["service"].update(targetPort=8080)),
    "does not name a Gateway": ("canary.yaml", lambda d: d["spec"]["service"]["gatewayRefs"][0].update(name="private")),
    "must rise strictly": ("canary.yaml", lambda d: d["spec"]["analysis"].update(stepWeights=[5, 20, 10])),
    "within 1..99": ("canary.yaml", lambda d: d["spec"]["analysis"].update(stepWeights=[50, 100])),
    "unsupported duration": ("canary.yaml", lambda d: d["spec"]["analysis"].update(interval="1m30s")),
    "does not admit routes": (
        "gateway.yaml",
        lambda d: d["spec"]["listeners"][0]["allowedRoutes"]["namespaces"]["selector"].update(matchLabels={"kubernetes.io/metadata.name": "prod"}),
    ),
}


@pytest.mark.parametrize("expected", list(CANARY_MUTATIONS))
def test_each_canary_mistake_is_caught(crd: Path, tmp_path: Path, expected: str) -> None:
    filename, change = CANARY_MUTATIONS[expected]
    errs = checks.check_all(mutated(tmp_path, filename, change), crd)
    if expected.startswith("schema:"):
        assert any(e.startswith("canary.yaml: ") and "Canary/" not in e for e in errs), errs
    else:
        assert any(expected in e for e in errs), errs


def test_schedule_and_durations() -> None:
    assert checks.expected_weights({"stepWeights": [5, 10, 20]}) == [5, 10, 20]
    assert checks.expected_weights({"stepWeight": 10, "maxWeight": 50}) == [10, 20, 30, 40, 50]
    with pytest.raises(ValueError, match="stepWeight"):
        checks.expected_weights({"maxWeight": 50})
    assert checks.seconds("15s") == 15
    assert checks.seconds("2m") == 120
    with pytest.raises(ValueError, match="unsupported"):
        checks.seconds("15")


def canary() -> checks.Doc:
    """The real Canary."""
    return next(d for d in checks.load(CONFIG) if d.kind == "Canary")


def rollout(weights: list[int], final: str = "Succeeded", route_sum: int = 100) -> list[dict[str, Any]]:
    """A synthetic observed rollout: two samples per step, then the final phase."""
    obs: list[dict[str, Any]] = [{"t": 0, "phase": "Succeeded", "weight": 0, "primary": 100, "canary": 0}]
    for i, w in enumerate(weights):
        for k in range(2):
            obs.append({"t": 15 * i + k, "phase": "Progressing", "weight": w, "primary": route_sum - w, "canary": w})
    obs.append({"t": 999, "phase": final, "weight": 0, "primary": 100, "canary": 0})
    return obs


def test_judge_accepts_the_expected_rollout_only() -> None:
    good = checks.judge(rollout([5, 10, 20, 40, 60, 80]), canary())
    assert good["pass"]
    assert good["observed"] == [5, 10, 20, 40, 60, 80]
    assert not checks.judge(rollout([5, 10, 40, 60, 80]), canary())["pass"]  # a skipped step
    assert not checks.judge(rollout([5, 10, 20], final="Failed"), canary())["pass"]  # rolled back
    assert not checks.judge(rollout([5, 10, 20, 40, 60, 80], route_sum=90), canary())["pass"]
    assert not checks.judge([], canary())["pass"]


def test_cli(crd: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    del crd  # downloaded by the fixture; the CLI reuses the cached file
    assert main(["--config", str(CONFIG), "--cache", str(ROOT / ".tmp"), "validate"]) == 0
    assert main(["--config", str(CONFIG), "schedule"]) == 0
    obs = tmp_path / "observed.jsonl"
    obs.write_text("\n".join(json.dumps(s) for s in rollout([5, 10, 20, 40, 60, 80])) + "\n", encoding="utf-8")
    assert main(["--config", str(CONFIG), "judge", str(obs)]) == 0
    bad = copy.deepcopy(rollout([5, 10]))
    obs.write_text("\n".join(json.dumps(s) for s in bad), encoding="utf-8")
    assert main(["--config", str(CONFIG), "judge", str(obs)]) == 1
    out = capsys.readouterr().out
    assert "0 violation(s)" in out
    assert "canary weights [5, 10, 20, 40, 60, 80] every 15s" in out
