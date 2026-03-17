# Contributing to Sqlery

Thanks for your interest in contributing to Sqlery. This guide covers everything you need to get started.

## Development Setup

**Requirements:** Python 3.10+, [uv](https://docs.astral.sh/uv/)

```bash
# Clone and install
git clone https://github.com/intrepid-g/sqlery.git
cd sqlery
uv pip install -e ".[dev]"
```

## Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=sqlery

# Run a specific test file
uv run pytest tests/test_core.py
```

## Code Style

This project uses **black** for formatting and **ruff** for linting. Both are configured with a line length of 100 characters targeting Python 3.10+.

```bash
# Format code
uv run black src/ tests/

# Lint code
uv run ruff check src/ tests/

# Lint and auto-fix
uv run ruff check --fix src/ tests/
```

Please ensure your code passes both formatters before submitting a PR.

## Type Hints

Sqlery uses full type annotations. Use modern built-in types (`list`, `dict`, `tuple`, `set`, `X | None`) rather than imports from `typing`.

## Pull Request Process

1. Fork the repository and create a feature branch from `master`.
2. Make your changes with clear, focused commits.
3. Add or update tests for any new or changed behavior.
4. Ensure all tests pass and code is formatted/linted.
5. Open a pull request with a brief description of the change and its motivation.

## Commit Messages

Use conventional commit format:

```
(type): short description
```

Types: `feat`, `fix`, `docs`, `test`, `chore`, `refactor`, `perf`

## Reporting Issues

Open an issue on GitHub with:

- A clear description of the problem or suggestion.
- Steps to reproduce (for bugs).
- Your Python version, OS, and database backend (PostgreSQL or SQLite).

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
