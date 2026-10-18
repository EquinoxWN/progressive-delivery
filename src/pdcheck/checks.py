"""Offline checks of the delivery configuration: Flagger's own schema, house rules for workloads,
cross-references between objects, the expected traffic-shift schedule, and the judgement of an
observed rollout recorded by the end-to-end run.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

FLAGGER_VERSION = "v1.45.0"
CRD_URL = f"https://raw.githubusercontent.com/fluxcd/flagger/{FLAGGER_VERSION}/artifacts/flagger/crd.yaml"
CRD_SHA256 = "58bd594b9e33deb0edceac81f6ed160b458a7933039fb295552724f656033ee5"
DIGEST = re.compile(r"^[a-z0-9.\-/:]+@sha256:[0-9a-f]{64}$")
DURATION = re.compile(r"^(\d+)(s|m|h)$")


@dataclass(frozen=True)
class Doc:
    """One Kubernetes object and the file it came from."""

    path: Path
    body: dict[str, Any]

    @property
    def kind(self) -> str:
        """The object's kind."""
        return str(self.body.get("kind", ""))

    @property
    def name(self) -> str:
        """metadata.name."""
        return str(self.body.get("metadata", {}).get("name", ""))

    @property
    def namespace(self) -> str:
        """metadata.namespace, empty for cluster-scoped objects."""
        return str(self.body.get("metadata", {}).get("namespace", ""))


def load(root: Path, dirs: tuple[str, ...] = ("app", "gateway")) -> list[Doc]:
    """Every object in the given config folders (kustomization files excluded)."""
    docs: list[Doc] = []
    for d in dirs:
        for p in sorted((root / d).glob("*.yaml")):
            if p.name == "kustomization.yaml":
                continue
            for body in yaml.safe_load_all(p.read_text(encoding="utf-8")):
                if not isinstance(body, dict) or "kind" not in body:
                    raise ValueError(f"{p}: every document must be a Kubernetes object")
                docs.append(Doc(p, body))
    return docs


def fetch_crd(cache: Path) -> Path:
    """Download Flagger's CRD file once over HTTPS and check its SHA-256."""
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"flagger-crd-{FLAGGER_VERSION}.yaml"
    if not out.exists():
        with urllib.request.urlopen(CRD_URL, timeout=30) as resp:
            data = resp.read(2_000_000)
        out.write_bytes(data)
    if hashlib.sha256(out.read_bytes()).hexdigest() != CRD_SHA256:
        out.unlink()
        raise ValueError("Flagger CRD does not match the pinned SHA-256")
    return out


def canary_schema(crd_file: Path) -> dict[str, Any]:
    """The OpenAPI v3 schema of Canary v1beta1 from Flagger's CRD file."""
    for crd in yaml.safe_load_all(crd_file.read_text(encoding="utf-8")):
        if isinstance(crd, dict) and crd.get("spec", {}).get("names", {}).get("kind") == "Canary":
            for v in crd["spec"]["versions"]:
                if v["name"] == "v1beta1":
                    schema: dict[str, Any] = v["schema"]["openAPIV3Schema"]
                    return schema
    raise ValueError("no Canary v1beta1 schema in the CRD file")


def schema_errors(doc: Doc, schema: dict[str, Any]) -> list[str]:
    """Violations of Flagger's schema."""
    v = jsonschema.Draft7Validator(schema)
    return [f"{doc.path.name}: {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in v.iter_errors(doc.body)]


def seconds(duration: str) -> int:
    """A Go-style duration of one unit (15s, 1m, 2h) in seconds."""
    m = DURATION.fullmatch(duration)
    if not m:
        raise ValueError(f"unsupported duration {duration!r}")
    return int(m.group(1)) * {"s": 1, "m": 60, "h": 3600}[m.group(2)]


def expected_weights(analysis: dict[str, Any]) -> list[int]:
    """The canary weights Flagger steps through, from stepWeights or stepWeight up to maxWeight."""
    if "stepWeights" in analysis:
        return [int(w) for w in analysis["stepWeights"]]
    step, top = int(analysis.get("stepWeight", 0)), int(analysis.get("maxWeight", 50))
    if step <= 0:
        raise ValueError("analysis needs stepWeights or a positive stepWeight")
    return list(range(step, top + 1, step))


def workload_rules(doc: Doc) -> list[str]:
    """House rules for a Deployment: pinned images, least privilege, probes and limits."""
    errs: list[str] = []
    pod = doc.body.get("spec", {}).get("template", {}).get("spec", {})
    where = f"{doc.path.name}: {doc.kind}/{doc.name}"
    psc = pod.get("securityContext", {})
    if psc.get("runAsNonRoot") is not True:
        errs.append(f"{where}: pod must set runAsNonRoot: true")
    if psc.get("seccompProfile", {}).get("type") != "RuntimeDefault":
        errs.append(f"{where}: pod must use the RuntimeDefault seccomp profile")
    if pod.get("automountServiceAccountToken") is not False:
        errs.append(f"{where}: service account token must not be mounted")
    for key in ("hostNetwork", "hostPID", "hostIPC"):
        if pod.get(key):
            errs.append(f"{where}: {key} is not allowed")
    if any("hostPath" in v for v in pod.get("volumes", [])):
        errs.append(f"{where}: hostPath volumes are not allowed")
    for c in pod.get("containers", []) + pod.get("initContainers", []):
        cw = f"{where} container {c.get('name')}"
        if not DIGEST.fullmatch(str(c.get("image", ""))):
            errs.append(f"{cw}: image must be pinned by sha256 digest")
        sc = c.get("securityContext", {})
        if sc.get("privileged"):
            errs.append(f"{cw}: privileged is not allowed")
        if sc.get("allowPrivilegeEscalation") is not False:
            errs.append(f"{cw}: allowPrivilegeEscalation must be false")
        if sc.get("readOnlyRootFilesystem") is not True:
            errs.append(f"{cw}: root filesystem must be read-only")
        if "ALL" not in sc.get("capabilities", {}).get("drop", []):
            errs.append(f"{cw}: capabilities must drop ALL")
        res = c.get("resources", {})
        for part, keys in (("requests", ("cpu", "memory")), ("limits", ("memory",))):
            for k in keys:
                if k not in res.get(part, {}):
                    errs.append(f"{cw}: resources.{part}.{k} is required")
        for probe in ("readinessProbe", "livenessProbe"):
            if probe not in c:
                errs.append(f"{cw}: {probe} is required")
    return errs


def canary_rules(canary: Doc, docs: list[Doc]) -> list[str]:
    """Cross-references and schedule sanity for a Canary."""
    errs: list[str] = []
    spec = canary.body.get("spec", {})
    where = f"{canary.path.name}: Canary/{canary.name}"
    target = spec.get("targetRef", {})
    deps = {(d.namespace, d.name): d for d in docs if d.kind == "Deployment"}
    dep = deps.get((canary.namespace, target.get("name")))
    if target.get("kind") != "Deployment" or dep is None:
        errs.append(f"{where}: targetRef must name a Deployment in namespace {canary.namespace}")
    else:
        ports = {p.get("containerPort") for c in dep.body["spec"]["template"]["spec"]["containers"] for p in c.get("ports", [])}
        if spec.get("service", {}).get("targetPort", spec.get("service", {}).get("port")) not in ports:
            errs.append(f"{where}: service targetPort is not a container port of the Deployment")
    gateways = {(d.namespace, d.name): d for d in docs if d.kind == "Gateway"}
    namespaces = {d.name: d for d in docs if d.kind == "Namespace"}
    for ref in spec.get("service", {}).get("gatewayRefs", []):
        gw = gateways.get((ref.get("namespace", canary.namespace), ref.get("name")))
        if gw is None:
            errs.append(f"{where}: gatewayRef {ref} does not name a Gateway in the config")
            continue
        for listener in gw.body["spec"].get("listeners", []):
            allowed = listener.get("allowedRoutes", {}).get("namespaces", {})
            labels = dict(namespaces.get(canary.namespace, Doc(canary.path, {})).body.get("metadata", {}).get("labels", {}))
            labels.setdefault("kubernetes.io/metadata.name", canary.namespace)
            if allowed.get("from") == "Selector" and not all(labels.get(k) == v for k, v in allowed.get("selector", {}).get("matchLabels", {}).items()):
                errs.append(f"{where}: Gateway {gw.name} listener {listener.get('name')} does not admit routes from {canary.namespace}")
            if allowed.get("from", "Same") == "Same" and gw.namespace != canary.namespace:
                errs.append(f"{where}: Gateway {gw.name} only admits routes from its own namespace")
    analysis = spec.get("analysis", {})
    try:
        weights = expected_weights(analysis)
        seconds(str(analysis.get("interval", "")))
    except ValueError as e:
        return [*errs, f"{where}: {e}"]
    if not weights or any(b <= a for a, b in itertools.pairwise(weights)) or weights[0] < 1 or weights[-1] > 99:
        errs.append(f"{where}: step weights must rise strictly within 1..99, got {weights}")
    return errs


def check_all(root: Path, crd_file: Path) -> list[str]:
    """Every check over the config folder; an empty list means it passes."""
    docs = load(root)
    schema = canary_schema(crd_file)
    errs: list[str] = []
    for d in docs:
        if d.kind == "Canary":
            errs += schema_errors(d, schema) + canary_rules(d, docs)
        elif d.kind == "Deployment":
            errs += workload_rules(d)
    if not any(d.kind == "Canary" for d in docs):
        errs.append("no Canary found")
    return errs


def judge(observed: list[dict[str, Any]], canary: Doc) -> dict[str, Any]:
    """Compare an observed rollout (one sample per poll) with the Canary's schedule."""
    want = expected_weights(canary.body["spec"]["analysis"])
    seen: list[int] = []
    for s in observed:
        w = int(s.get("weight", 0))
        if s.get("phase") == "Progressing" and w > 0 and (not seen or seen[-1] != w):
            seen.append(w)
    route_ok = all(int(s["primary"]) + int(s["canary"]) == 100 for s in observed if "primary" in s and "canary" in s)
    route_weights = sorted({int(s["canary"]) for s in observed if "canary" in s} - {0})
    final = observed[-1].get("phase") if observed else None
    ok = seen == want and final == "Succeeded" and route_ok and route_weights == sorted(want)
    return {"expected": want, "observed": seen, "route_canary_weights": route_weights, "route_sums_100": route_ok, "final_phase": final, "pass": ok}


def read_observed(path: Path) -> list[dict[str, Any]]:
    """Samples written by the end-to-end run, one JSON object per line."""
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError("every line must be a JSON object")
            out.append(item)
    return out
