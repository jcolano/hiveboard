# Contributing to HiveBoard

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/jcolano/hiveboard.git
cd hiveboard

# Create a virtual environment
python -m venv venv
source venv/bin/activate  # Linux/macOS
# or: venv\Scripts\activate  # Windows

# Install all dependencies (backend + SDK + dev tools)
pip install -e ".[backend,dev]"
pip install -e "./src/sdk"

# Copy config template
cp config.example.json config.json
# Edit config.json with your dev settings (see config.example.json for options)

# Start the server
cd src
uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
```

## Running Tests

```bash
pytest tests/
```

## Code Style

- Python 3.11+
- Use type hints for function signatures
- Follow existing async/await patterns with FastAPI
- Keep route handlers in the appropriate module under `src/backend/routes/`
- Use `ruff` for linting: `ruff check src/`

## Pull Request Process

1. Fork the repo and create a feature branch from `main`
2. Make your changes with clear, descriptive commits
3. Add or update tests for new functionality
4. Ensure `pytest` and `ruff check` pass
5. Submit a PR with a clear description of what and why

## Reporting Issues

- Use GitHub Issues for bug reports and feature requests
- Include steps to reproduce for bugs
- Include your Python version and OS

## Project Structure

Route handlers live in `src/backend/routes/`. Shared helpers are in `src/backend/routes/helpers.py`. The main app setup (lifespan, middleware, error handlers) is in `src/backend/app.py`.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
