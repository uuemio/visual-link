# Repository Guidelines

## Project Structure

`src/visual_link/` contains the application. `core/` implements image coding, protocols and file assembly; `backends/` provides Windows and Linux adapters. `configs/` contains device examples, `scripts/` provides launch and deployment tools, and `docs/` describes installation and protocols.

## Development Commands

Install with `python -m pip install -e .`. Run the local page with `python -m visual_link.web` from the repository directory. Run regression checks with `python -m unittest discover -s tests -v` and inspect dependencies with `python -m pip check`.

## Coding Style

Use four-space indentation, `snake_case` functions, `PascalCase` classes and uppercase constants. Follow surrounding code and keep changes focused. Reuse existing platform and protocol interfaces. Keep configuration examples portable.

## Verification

Regression cases use `unittest` and `test_*.py` filenames. Keep fixtures required by active cases. Select checks relevant to the changed behavior; capture and display changes also require physical device verification.

## Commits and Documentation

Use concise imperative commit subjects describing the resulting behavior. Describe changes and verification in pull requests. Update installation and usage instructions when user workflows change. Keep credentials, virtual environments, transfer outputs and machine-specific paths out of source commits.
