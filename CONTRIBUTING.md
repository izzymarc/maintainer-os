# Contributing to MaintainerOS

Thank you for contributing. The project is early-stage, so design discussion is valuable before large implementation work.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
pytest
```

Keep changes focused, add tests for behavior changes, and update documentation when an API or security assumption changes.

## Pull requests

Please explain:

- the problem and intended behavior;
- security or permission implications;
- test coverage and any limitations;
- follow-up work that is intentionally out of scope.

Do not include credentials, private repository data, or model-provider secrets in issues, logs, fixtures, or commits.
