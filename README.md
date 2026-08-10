# BATM tag asset manager

this is a WIP OF BATM (Batch Asset Tag Manager) is a Blender 5.2+ extension for managing Asset
Browser tags at library scale. It analyzes selected assets in background
Blender workers, proposes Tag changes through an editable Review, backs up
every change, and applies them with fingerprint verification — with automatic
rollback if anything fails.

## Workflow

1. **Select assets** in the Asset Browser sidebar, then *Analyze and Review*.
2. BATM fingerprints the files, runs AutoTag rules + the bundled Knowledge
   dictionary on deep asset facts (hierarchy, constraints, bones, materials…).
3. **Review**: inspect every proposed change, toggle assets, adjust the
   Sanitizer settings, add/remove tags per asset.
4. **Confirm and Apply** creates a verified backup and runs worker processes
   to write the tags. Every written asset is re-read and verified.
5. **Diagnostics & Logs** section shows summaries, event log and allows
   restoring the latest backup.

## Modes

- **AutoTag**: deterministic rules (see `docs/AUTOTAG_RULES.md`) over
  facts extracted without opening .blend files (only `assets_only` reads).
- **Knowledge dictionary**: bundled category Tags matched by words from names
  and materials (`resources/autotag_knowledge.json`).
- **Manual Tag Editor**: unified Tag list for the current selection with
  Add / Remove / Replace queued as operations reviewed before applying.
- **Sanitizer**: casing, separators, blacklist, max Tag length (63), synonym
  merging and a hard per-asset cap to avoid Tag flooding.

## Safety

- Every run creates a checksummed, timestamped backup manifest.
- Workers are killed by the watchdog deadline if they hang.
- Applying verifies fingerprints before and after each file write.
- Cancel mid-execution rolls back every file already written and verifies.

## Configuration (add-on preferences)

- `Max Workers`: 1–4 parallel background processes.
- `Worker Timeout`: seconds per request before a worker is killed.
- `Review Page Size`, `Log Retention Days / Runs`.

## Development

Run the offline test-suite without Blender:

```
python tests/test_sanitizer.py
python tests/test_extractors_contract.py
python tests/test_autotag_pipeline.py
python tests/test_knowledge.py
python tests/test_scheduler.py
python tests/test_rules_hierarchy.py
python tests/test_ipc.py
```

All tests are pytest- and unittest-compatible plain asserts so they run in CI
without external dependencies. See `docs/` for rules and knowledge schemas.
