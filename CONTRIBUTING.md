# Contributing

Thanks for helping. This repository is Kubernetes and Flagger configuration with Python checks;
issues and pull requests are welcome.

## Set up and check your change

Needs Python 3.11+, make and bash with curl; `make e2e` also needs Linux with Docker.

```bash
make setup      # check tools
make lint       # yamllint, ruff, mypy, pdcheck validate, kubeconform
make test       # tests, including the mutation tests
make e2e        # a real rollout on kind (Linux + Docker)
```

A pull request is ready when `make lint` and `make test` pass and CI (including the rollout) is green.

## Guidelines

- Pin every new image by digest and every new tool by version and SHA-256 (see `scripts/tools.sh`).
- A new house rule in `pdcheck` needs a mutation test that breaks it.
- Changes to the Canary's schedule must keep `pdcheck schedule` and the e2e judge in agreement;
  update `docs/results/` with the new schedule.
- Significant design changes need an ADR in `docs/adr/` (copy `0001` for the format).
- Keep function comments to one short phrase; let names and tests explain the rest.
- Report security problems privately, as described in [SECURITY.md](SECURITY.md).

By contributing you agree that your work is released under the [MIT License](LICENSE).
