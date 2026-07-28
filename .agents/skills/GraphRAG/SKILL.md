```markdown
# GraphRAG Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill teaches the core development patterns and conventions used in the GraphRAG Python codebase. You'll learn how to structure files, write imports and exports, follow commit conventions, and understand the project's approach to testing. This guide is designed to help you quickly become productive and consistent when contributing to GraphRAG.

## Coding Conventions

### File Naming
- Use **snake_case** for all file and module names.
  - Example: `graph_utils.py`, `data_loader.py`

### Import Style
- Prefer **relative imports** within the package.
  - Example:
    ```python
    from .utils import process_data
    from ..models import GraphNode
    ```

### Export Style
- Use **named exports** (explicitly list exported functions, classes, etc.).
  - Example:
    ```python
    __all__ = ["GraphBuilder", "parse_graph"]
    ```

### Commit Messages
- Follow **conventional commit** format.
- Use the `feat` prefix for new features.
  - Example:
    ```
    feat: add support for multi-edge graphs
    ```

## Workflows

### Adding a New Feature
**Trigger:** When implementing a new capability or module  
**Command:** `/add-feature`

1. Create a new Python file using snake_case naming.
2. Use relative imports to reference internal modules.
3. Implement your feature, exporting key classes/functions via `__all__`.
4. Write or update relevant tests (see Testing Patterns).
5. Commit using the conventional format:
    ```
    feat: short description of the feature
    ```
6. Push your branch and open a pull request.

### Refactoring Code
**Trigger:** When improving code structure or readability  
**Command:** `/refactor`

1. Identify the code to refactor.
2. Apply changes, ensuring file naming and import conventions are maintained.
3. Update `__all__` exports if necessary.
4. Run all relevant tests.
5. Commit with a descriptive message (e.g., `refactor: improve graph traversal logic`).
6. Push changes and open a pull request.

### Writing Tests
**Trigger:** When adding or updating tests  
**Command:** `/write-test`

1. Create or update test files (see Testing Patterns).
2. Ensure tests cover new or changed functionality.
3. Run tests locally to verify correctness.
4. Commit with a message like `test: add tests for graph merging`.

## Testing Patterns

- Test files follow the `*.test.ts` pattern (TypeScript), though the main codebase is Python.
- The specific testing framework is **unknown**; check existing test files for patterns.
- Place test files alongside the code they test, or in a dedicated `tests/` directory if present.

Example test file name:
```
graph_builder.test.ts
```

## Commands
| Command        | Purpose                                   |
|----------------|-------------------------------------------|
| /add-feature   | Start the workflow for adding a new feature|
| /refactor      | Begin a code refactoring workflow         |
| /write-test    | Guide for writing or updating tests       |
```
