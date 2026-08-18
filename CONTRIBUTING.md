# Contributing

Thank you for helping improve Queensland Train Tracker.

## Development setup

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -e '.[dev]'
```

Before opening a pull request, run:

```bash
pytest
ruff check .
mypy -p train_tracker
```

## Project expectations

- Keep the logical LED framebuffer exactly 128×64 unless a change explicitly introduces another
  hardware profile.
- Keep providers, layout, tracking, and rendering independent from Tkinter.
- Keep automated tests deterministic and network-free.
- Label fictional fixtures and demonstration stations as `Demo`.
- Never describe schedule-interpolated movement as physically observed.
- Do not commit downloaded GTFS archives, generated SQLite databases, credentials, caches, virtual
  environments, temporary screenshots, or raw recordings.
- Do not use Translink or Queensland Government logos/artwork without permission.
- Add focused tests for new sprite states, layouts, provider behavior, or hardware mappings.

## Media

README GIFs must be generated with `train-tracker record-demo` from working renderers. Source frames
must be native 128×64 and enlarged only with nearest-neighbour scaling. Keep output readable and
reasonably small.

## Pull requests

Explain what changed, why, how it was verified, and any hardware or live-feed behavior that remains
untested. Small, reviewable commits are preferred.
