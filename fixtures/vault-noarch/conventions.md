---
tags: [jevgate/conventions]
---

# Conventions

How code in Ledger is written.

## Conventions

- Public functions carry type hints and a one-line docstring. #jevgate/convention #jevgate/applies/py
- Errors are raised as `LedgerError` subclasses; handlers translate them, services never catch broadly. #jevgate/convention #jevgate/applies/py
- Tests live next to the module they cover as `test_<module>.py` and run with pytest. #jevgate/convention
- Commit messages are imperative and under 72 characters on the first line. #jevgate/convention
- Log lines are structured key=value pairs, never f-strings with embedded values. #jevgate/convention
