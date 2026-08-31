# BATM tag asset manager

> **Sponsored by b-water Animation Studios** 

BATM (Batch Asset Tag Manager) is a Blender 5.2+ extension for managing Asset
Browser tags at library scale. It analyzes selected assets in background
Blender workers, proposes Tag changes through an editable Review, backs up
every change, and applies them with fingerprint verification — with automatic
rollback if anything fails.

## Workflow

1. **Select assets** in the Asset Browser (browse_mode == ASSETS), then press the
   large **Run BATM — Analyze, Review & Apply Tags** button.
   > *What it does:* fingerprints every `.blend`, runs the AutoTag engine on deep
   > facts (object types, rig/bones, modifiers, materials, poly count, animation,
   > UVs, collection hierarchy, library/catalog), proposes tags, then opens an
   > editable Review. Nothing is written until you press **Confirm and Apply**.
2. **Review** (*engine/reducer.py*): inspect every proposed change, filter tags
   by **name** (free-text search) and **character count** (`tag_char_length`),
   toggle assets on/off, paginate, adjust Sanitizer settings, add/remove tags per asset.
   - `Both` (default ON): for compounds like `rosarojavioleta` emits the combined
     form `Rosa Roja Violeta` **and** the atomic tags `Rosa`, `Roja`, `Violeta`.
3. **Confirm and Apply** creates a verified, checksummed backup and runs worker
   processes to write the tags. Every written asset is re-read and verified.
4. **Diagnostics & Logs** shows summaries, event log and allows restoring the
   latest backup.
   
## Modes

- **AutoTag**: deterministic rules (`docs/AUTOTAG_RULES.md`) over
  facts extracted without opening .blend files (only `assets_only` reads).
- **Knowledge custom dictionary**: bundled category Tags matched by words from names
  and materials (`resources/autotag_knowledge.json`).
- **Manual Tag Editor**: unified Tag list for the current selection with
  Add / Remove / Replace queued as operations reviewed before applying.
  Filter tags by name (`tag_search`) and by character count (`tag_length_filter`) on preview and manual tag editor.
- **Sanitizer**: casing, separators, blacklist, max Tag length (63), synonym
  merging, compound recombine (`BOTH`/`SPLIT`/`RECOMBINED`) and a hard per-asset cap.

## Safety

- Every run creates a checksummed, timestamped backup manifest.
- Workers are killed by the watchdog deadline if they hang.
- Applying verifies fingerprints before and after each file write.
- Cancel mid-execution rolls back every file already written and verifies.

## Configuration (add-on preferences (advanced))

- `Max Workers`: 1–4 parallel background processes.
- `Worker Timeout`: seconds per request before a worker is killed.
- `Review Page Size`, `Log Retention Days / Runs`.
- `Catalog level` (Concepts / Full) + `Cross-Tagging`.


## Sponsors

BATM is proudly **sponsored by b-water Animation** https://b-waterstudios.com
