# Chat Workbench

A small local task page for keeping track of work spread across AI conversations.

Each task holds its next step, acceptance criteria, conversation link, result link and notes. Data stays in a JSON file on your computer. The app does not read conversation content or call an AI service.

**Status:** v0.1.0-alpha.1, an early release for independent-use testing. The interface is currently in Chinese. [中文说明](README.md)

## Run

Requires Python 3.10+ and a recent browser supporting dialog, structuredClone and crypto.randomUUID. There are no third-party runtime dependencies.

~~~bash
python3 server.py --open
~~~

On Windows, use py -3 instead of python3. Open http://127.0.0.1:8765 and keep the terminal running. Press Ctrl+C to stop. If the port is busy, add --port 8766. Open the HTTP address rather than index.html directly.

Verified on macOS with Python 3.11.15 and 3.13.2. Windows has not yet been tested on a real Windows machine.

## Data

- Starts with an empty workspace.
- Current snapshot: data/workspace.json.
- Previous snapshots: data/backups/, copied before each successful save.
- Import and export JSON using the page buttons.
- A stale save receives HTTP 409 without replacing the latest snapshot.
- A failed save can be exported as a draft, preserving the most recent submitted task candidate.
- Export uses compact JSON so valid near-limit snapshots remain importable.
- Use WORKBENCH_DATA to choose a separate data directory. A process lock rejects a second server using the same directory.

The [example](examples/workspace.json) is synthetic. Importing a file replaces the current workspace after backing it up.

This is a single-user loopback service, not a hosted application. Local data is not encrypted. Conversation links may require signing in to the original service. This version does not migrate data from the earlier personal edition.

## Tests

~~~bash
python3 -m unittest -v test_server.py
~~~

Tests use temporary directories. See [CONTRIBUTING.md](CONTRIBUTING.md) for a small, reproducible contribution workflow.

Development used AI assistance. No external adoption or productivity claims have been verified. Released under the [MIT License](LICENSE).
