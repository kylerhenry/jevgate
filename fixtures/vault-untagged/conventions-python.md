---
area: conventions
applies_to: ["*.py"]
---

# Python conventions

## Conventions

- Public functions carry type hints and a one-line docstring.
- Errors are raised as `LedgerError` subclasses; handlers translate them, services never catch broadly.
