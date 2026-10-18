.PHONY: setup lint test kubeconform schedule e2e audit ci

PY ?= python
KUBECONFORM := .tmp/bin/kubeconform$(if $(filter Windows_NT,$(OS)),.exe,)
CRD_CATALOG := https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json
export PIP_CACHE_DIR := $(CURDIR)/.tmp/pip
export PYTHONDONTWRITEBYTECODE := 1

setup:
	mkdir -p .tmp
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]" "setuptools>=80"

# yamllint, ruff, mypy --strict, then the manifests against Flagger's pinned schema and the house
# rules (pdcheck) and against Kubernetes 1.36 and CRD schemas (kubeconform).
lint: kubeconform
	$(PY) -m yamllint .
	$(PY) -m ruff check . && $(PY) -m ruff format --check . && $(PY) -m mypy
	$(PY) -m pdcheck validate

kubeconform:
	bash scripts/tools.sh kubeconform
	mkdir -p .tmp/kubeconform-cache
	$(KUBECONFORM) -strict -summary -cache .tmp/kubeconform-cache -kubernetes-version 1.36.4 \
	  -schema-location default -schema-location '$(CRD_CATALOG)' -ignore-filename-pattern kustomization \
	  config/app config/gateway

test:
	$(PY) -m pytest -q

schedule:
	$(PY) -m pdcheck schedule

# Needs Linux and Docker: kind cluster, Envoy Gateway, Flagger, a real canary rollout (CI).
e2e:
	PY=$(PY) bash scripts/e2e.sh

audit:
	$(PY) -m pip_audit --skip-editable --cache-dir .tmp/pip-audit

ci: setup lint test
