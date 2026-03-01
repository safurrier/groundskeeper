# Project Commands (mise task runner)
- Build/setup: `mise run setup`
- Run all checks: `mise run check`
- Format code: `mise run format`
- Type check: `mise run ty`
- Lint code: `mise run lint`
- Test all: `mise run test`
- Test e2e: `mise run test:e2e` (requires `mise run install` first)
- Test everything: `mise run test:all`
- Test single: `uv run -m pytest tests/path_to_test.py::test_function_name`
- Install CLI: `mise run install`
- Dev container: `mise run dev:env`
- Docs serve: `mise run docs:serve`
- Docs build: `mise run docs:build`

# Code Style
- **Types**: Strict typing with ty, use proper annotations from `typing` module
- **Imports**: Standard lib first, third-party second, project imports last
- **Formatting**: Enforced by ruff formatter
- **Naming**: snake_case for functions/variables, PascalCase for classes
- **Python Version**: 3.10+
