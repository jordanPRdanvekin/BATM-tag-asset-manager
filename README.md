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
   latest backup. `Sponsored by b-water Studios Animation` is shown in the
   footer of the main panel and in Add-on Preferences.

## AutoTag — Deterministic, Zero Hallucination

- **Trie-based segmentation** (`engine/segmenter.py`): O(L) compound splitting
  via prefix tree — `rosarojavioleta` → `rosa` + `roja` + `violeta` (and combined).
- **Lemma-Dict** (`engine/lemma.py` + `resources/lemma_map.json`): exact
  dictionary lemmatization replaces Porter Stemmer — no more `procedural→procedur`.
- **Mutex Fauna / Domain Resolution** (`engine/taxonomy.py`): if Fauna wins,
  Human tags are suppressed; `child`→`cub`, `old prop`→`weathered` (not `elder`).
- **Corpus 5000+ tokens** across 15 domains + 26 knowledge groups + curated
  `compound_splits.json` (~100+ entries) and `segmenter_lexicon.json` (~500+).
- **Rules** are deterministic, versioned (schema 1), priority-sorted, with
  stem/lemma fallback only as last resort.
- **Catalog Sync** bidireccional with `blender_assets.cats.txt`.

## Modes

- **AutoTag**: deterministic rules (`docs/AUTOTAG_RULES.md`) over
  facts extracted without opening .blend files (only `assets_only` reads).
- **Knowledge dictionary**: bundled category Tags matched by words from names
  and materials (`resources/autotag_knowledge.json`).
- **Manual Tag Editor**: unified Tag list for the current selection with
  Add / Remove / Replace queued as operations reviewed before applying.
  Filter by name (`tag_search`) and by character count (`tag_length_filter`).
- **Sanitizer**: casing, separators, blacklist, max Tag length (63), synonym
  merging, compound recombine (`BOTH`/`SPLIT`/`RECOMBINED`) and a hard per-asset cap.

## Safety

- Every run creates a checksummed, timestamped backup manifest.
- Workers are killed by the watchdog deadline if they hang.
- Applying verifies fingerprints before and after each file write.
- Cancel mid-execution rolls back every file already written and verifies.

## Configuration (add-on preferences)

- `Max Workers`: 1–4 parallel background processes.
- `Worker Timeout`: seconds per request before a worker is killed.
- `Review Page Size`, `Log Retention Days / Runs`.
- `Catalog level` (Concepts / Full) + `Cross-Tagging`.

## Development

Run the offline test-suite without Blender:

```
pytest tests/ -v
```

All tests are pytest-compatible plain asserts so they run in CI without
external dependencies. See `docs/` for rules and knowledge schemas.

## Sponsors

BATM is proudly **sponsored by b-water Animation** https://b-waterstudios.com
