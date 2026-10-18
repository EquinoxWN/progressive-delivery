# Security policy

## Reporting a vulnerability

Please report security problems privately through GitHub:
**Security > Report a vulnerability** on this repository
(`https://github.com/EquinoxWN/progressive-delivery/security/advisories/new`). Do not open a public issue.

Include what you found, how to reproduce it, and the impact you expect. You will get an answer
within 7 days. Fixes are released as soon as they are ready, and you are credited unless you ask
not to be.

## Supported versions

This project is pre-1.0. Only the latest commit on `main` receives fixes.

## Scope

- **In scope:** manifests that grant more than they need (privileges, host access, unpinned
  images), routing that exposes the app beyond the intended Gateway, unpinned or unverified
  downloads in the scripts, and checks in `pdcheck` that pass something they should refuse.
- **Out of scope:** vulnerabilities in Flagger, Envoy Gateway, kind or podinfo themselves (report
  those upstream), and the absence of metric checks in M1 (planned for M2).

## How this repository protects itself

- The app namespace enforces the `restricted` Pod Security standard; the workload runs non-root
  with seccomp, a read-only root, no privilege escalation, all capabilities dropped, no service
  account token, and requests and limits.
- `pdcheck validate` refuses tag-only images, privileged or host-access pods and missing probes or
  limits; 24 mutation tests keep those rules working.
- Images and the kind node are pinned by digest; Flagger's CRD and every downloaded tool by
  SHA-256; Helm charts by version.
- The Gateway admits routes only from the labelled app namespace.
- Every GitHub Action is pinned to a full commit SHA, and workflows run with read-only
  permissions and without persisted credentials.
- Dependabot update pull requests are paused (`open-pull-requests-limit: 0`) so the repository keeps a single `main` branch; dependencies are updated by hand and checked by the CI audit.
- CI runs yamllint, ruff, mypy, pdcheck, kubeconform, the tests and pip-audit on every push and pull request.
