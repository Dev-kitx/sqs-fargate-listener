# 🤝 Contributing to sqs-fargate-listener

Thank you for taking the time to contribute! This project welcomes bug reports, feature requests, and pull requests.

---

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Running Tests](#running-tests)
- [Submitting Changes](#submitting-changes)
- [Coding Standards](#coding-standards)
- [Reporting Issues](#reporting-issues)

---

## 📜 Code of Conduct

Please be respectful and constructive. This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/).

---

## 🚀 Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/sqs-fargate-listener.git
   cd sqs-fargate-listener
   ```
3. Add the upstream remote:
   ```bash
   git remote add upstream https://github.com/pylearn-devops/sqs-fargate-listener.git
   ```

---

## 🛠️ Development Setup

This project uses [Pipenv](https://pipenv.pypa.io) for dependency management.

```bash
# Install all dev dependencies
make setup-env

# Or manually
pip install pipenv
pipenv install --dev
```

---

## 🧪 Running Tests

### Unit Tests

```bash
make run-pytest
```

This runs `pytest` with coverage and outputs a `coverage.xml` report.

### End-to-End Tests (LocalStack)

Make sure Docker and the AWS CLI are installed and configured.

```bash
# Start LocalStack + create queue + run worker + send message
make test

# Individual steps
make localstack   # Start LocalStack only
make queue        # Create the SQS test queue
make up           # Build and start the worker container
make send         # Send a test message to the queue
make logs         # Tail worker logs
make down         # Stop all containers
```

> **Note:** The default queue is `test-queue` in `us-east-1`. Override with `QUEUE_NAME`, `REGION`, or `ENDPOINT` env vars.

---

## 📤 Submitting Changes

1. Create a new branch from `master`:
   ```bash
   git checkout -b feat/your-feature-name
   ```
2. Make your changes and ensure:
   - All existing tests pass (`make run-pytest`)
   - New behaviour is covered by tests
   - Code is linted (`pipenv run ruff check src/ tests/`)
3. Commit with a clear message following [Conventional Commits](https://www.conventionalcommits.org/):
   ```
   feat: add support for FIFO queues
   fix: handle empty message body gracefully
   docs: update IAM permissions section
   ```
4. Push your branch and open a **Pull Request** against `master`.
5. Fill in the PR description explaining the _what_ and _why_.

---

## 🎨 Coding Standards

| Tool     | Purpose              | Run with                            |
|----------|----------------------|-------------------------------------|
| `ruff`   | Linting & formatting | `pipenv run ruff check src/ tests/` |
| `mypy`   | Type checking        | `pipenv run mypy src/`              |
| `pytest` | Unit tests           | `make run-pytest`                   |

- Keep functions small and focused.
- Add type annotations to all public functions and methods.
- Document public APIs with docstrings.
- Keep PRs focused — one logical change per PR.

---

## 🐛 Reporting Issues

Open an issue at [GitHub Issues](https://github.com/pylearn-devops/sqs-fargate-listener/issues) and include:

- A clear title describing the problem.
- Steps to reproduce.
- Expected vs actual behavior.
- Python version, OS, and package version (`pip show sqs-fargate-listener`).
- Relevant logs or tracebacks.

---

## 🙏 Thank You

Every contribution — no matter how small — makes this project better. We appreciate your time and effort!
