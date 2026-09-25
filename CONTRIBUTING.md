# Contributing

Contributions are welcome when they preserve the project's clean public-data
and safety boundaries.

## Before submitting

1. Open an issue for protocol changes, new transports, write behavior, or
   registry schema changes so the evidence and compatibility impact can be
   discussed first.
2. Keep the change focused and include synthetic tests.
3. Classify protocol claims as hardware-validated, statically derived, or
   hypothetical. State the method without publishing private source material.
4. Do not claim write safety from static definitions or a successful frame
   exchange alone.

## Publication boundary

Do not commit vendor applications or binaries, decompiled code, proprietary
source documents or formats, keys, authentication constants, credentials,
captures, device addresses, serial numbers, customer data, source-machine
paths, or raw runtime values. Short normalized labels and independently stated
technical facts are acceptable when licensing permits them.

Fixtures must be synthetic. If a bug can only be demonstrated with a real
capture or identifier, reduce it to the smallest invented equivalent before
committing it. Security-sensitive material belongs in a private report as
described in `SECURITY.md`.

## Checks

Use Python 3.11 or newer and install the development dependencies:

```console
python -m pip install -e '.[dev,ble]'
ruff check .
ruff format --check .
mypy src/openrbus
pytest
python tools/check_publication.py
python -m build
```

The publication audit is a guardrail, not proof that a contribution is legally
or operationally safe. Review the tracked diff and both built archives before
submission.

Source code contributions are accepted under Apache-2.0. Changes to the public
registry dataset are accepted under CC BY 4.0 and must retain attribution.
