---
area: conventions
applies_to: ["*.py"]
---

# Python conventions

## Conventions

- Public functions in production modules carry type hints and a one-line docstring; test functions in `test_*.py` are exempt.
- Errors are raised as `LedgerError` subclasses; handlers translate them, services never catch broadly.
