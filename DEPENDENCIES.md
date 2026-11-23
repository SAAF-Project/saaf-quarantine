# Dependencies and supported checks

Runtime and tests use the Python standard library. No model SDK, API key or network
connection is required. CI runs Python 3.11 and 3.12 on Ubuntu 24.04; passing those
checks does not establish support for other interpreters or operating systems.

GitHub Actions are pinned to commit revisions in `.github/workflows/`. Review
upstream changes and rerun the same synthetic tests before updating a pin. Hosted
runner images and Python patch releases are still supplied by GitHub; this is not
a fully hermetic environment or a transitive dependency lock.

`pyproject.toml` describes optional package installation. Direct checker execution
and copying `saaf_quarantine.py` do not require setuptools at runtime. The checker
reads Python, notebooks and JavaScript/TypeScript as text; it never imports the
agent repository or installs that repository's dependencies.
