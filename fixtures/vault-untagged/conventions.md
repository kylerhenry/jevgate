---
area: conventions
---

# Conventions

How code in Ledger is written.

## Conventions

- Tests live next to the module they cover as `test_<module>.py` and run with pytest.
- Commit messages are imperative and under 72 characters on the first line.
- Log lines are structured key=value pairs, never f-strings with embedded values.
