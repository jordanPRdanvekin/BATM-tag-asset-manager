# BATM — Batch Asset Tag Manager `v3.0.0`

> **BATM was developed during an internship and is sponsored by B-Water Studios.**  
> https://b-waterstudios.com/

BATM is a Blender **5.2 LTS** Extension for managing Asset Browser Tags at library scale. It analyses selected assets in background Blender workers, proposes Tag through an editable Review, backs up every change with a checksummed manifest, and applies them with fingerprint verification — with automatic rollback if anything fails.

Only Tag collections are ever written. Geometry, materials, rigs, catalogs and previews are read-only analysis context.

## Requirements

- **Blender 5.2 LTS** (`blender_version_min = "5.2.0"` in `blender_manifest.toml`)
- 
## Installation

BATM ships as a Blender Extension (`blender_manifest.toml` at the archive root).

1. Build or download `batch_asset_tag_manager-3.0.0.zip` (the manifest must be at the zip root).
2. In Blender: *Edit → Preferences → Get Extensions → Install from Disk* and select the zip.
3. Enable the extension. The panel appears in **Asset Browser → Sidebar → BATM** when `browse_mode == "ASSETS"`.
4. Alternatively, drag the zip onto the Blender window.

Permissions: `files = "Read asset libraries and store BATM rules backups and logs"` (filesystem access for libraries, user rules, backups and logs).

## Workflow

1. **Select assets and library** in the Asset Browser, then press the large **Run BATM** button.  
   *What it does:* fingerprints every `.blend`, extracts deep facts without opening files (`bpy.data.libraries.load(..., assets_only=True)`), runs the AutoTag engine (object types, rig/bones, modifiers, materials, poly count, animation, UVs, collection hierarchy, library/catalog), proposes Tags, then opens an editable Review. Nothing is written until you press **Confirm and Apply**.

2. **Review** (`core/reducer.py` + `ui/review.py`): inspect every proposed change, filter Tags by name (free-text search) and character count (`tag_char_length`, alnum only), toggle assets on/off, paginate, adjust Sanitizer settings, add/remove Tags per asset. Each Tag shows its reason (rule / knowledge / taxonomy / manual).  
   `Both` (default ON, `recombine = "BOTH"`): for compounds like `rosarojavioleta` emits the combined form `Rosa Roja Violeta` **and** the atomic Tags `Rosa`, `Roja`, `Violeta`.

3. **Confirm and Apply** creates a verified, checksummed backup and runs background workers (`worker/batm_worker.py` via `Popen` + `--factory-startup --background`) to write the Tags. Every written asset is re-read and verified. Progress is reported via `WindowManager.progress_begin/update/end` and `bpy.app.timers` (main thread only, never `bpy` from threads).

4. **Diagnostics & Logs** summarises tags (total / unique / duplicate / empty / invalid), pending operations, warnings and errors, and allows restoring or discarding the latest backup. The backup is deleted only after a fully verified success.

## Features

- **AutoTag** — deterministic, explainable rules over `core/fields.py` match fields (19 fields including `bone_names`, `constraint_types`, `parent_names`, `hierarchy_depth`, `poly_count_range`, `has_animation`, `has_uvs`, `vertex_count`, `collection_names`). Deduplication keeps the lowest priority and merges explanations. Prefix detection (`SM_`, `SK_`, `M_`, `T_`, `A_`, `FX_`, `SC_`, `BP_`, `HDRI_`, `KIT_`, `GN_`, `Rig_`) and poly-range / animation / rig presentation (`Rigged` / `Biped` / `Quadruped`, the latter only from affirmative `rig_topology`).

- **Segmentation** — prudent Trie + curated `compound_splits.json` (209 fixups) and a 310-entry `lemma_map.json` (identity maps keep `procedural` intact, Porter stemmer is fallback only). Mode `Both` emits the combined unit and the atomic components when a real glued compound is found.

- **Knowledge & Taxonomy** — bundled `autotag_knowledge.json` (33 groups, 863 words), `taxonomy.json` (19 domains, 1649 words, aliases including Spanish `grulla`, `zorro`, `cigüeña`), `segmenter_lexicon.json` (2502 words) and `autotag_rules.json` (23 validated rules). User overrides live in `user_taxonomy_override.json` under the extension storage dir.

- **Manual Tag Editor** — unified, searchable, paginated Tag frequency list (`1/N` … `N/N` coverage, search + character-count filter with `tag_length_filter`, min/max range filter in Review detail), multi-select, Add / Remove / Replace queued as operations and shown together with AutoTag proposals in Preview. No Tag is written before approval.

- **Sanitizer** (`core/sanitizer.py`) — split on `COMMA_SEMICOLON` (or `DOUBLE_HYPHEN` with hyphen sub-split), collapse whitespace, apply casing (`TITLE` default, plus `LOWER`, `UPPER`, `SNAKE`, `KEBAB`, `PASCAL`, `CAMEL`, `NONE`), deduplicate via `casefold`, optional synonym merge, optional compound split, hard `TAG_MAX_LENGTH = 63` (Blender limit, truncates with warning), hard `sanitize_max_tags` cap per asset (slice after alphabetical sort when `sort=True`), deterministic output.

- **Transactional execution** — `discover → analyse → propose → sanitise → Review → approve → plan by .blend → backup (checksummed JSON, atomic write) → write per file (single writer per `.blend`) → verify → refresh (`bpy.ops.asset.library_refresh()` once, via `temp_override`) → cleanup. Cancel or failure triggers verified rollback; every state goes through `core/state_machine.py` (`IDLE` → `ANALYZING` → `REVIEW_READY` → `APPROVED` → `BACKED_UP` → `EXECUTING` → `SUCCEEDED`/`FAILED`/`RESTORING` → `REFRESHING` → `IDLE`).

- **Safety & storage** — atomic `storage.atomic_json_write` (Windows `WinError 5` retry), `BATM_BASE_DIR` env override for tests (traversal-checked), `worker_ipc` validated `run_id` and `runs/<id>` containment, fingerprint (`sha256` + size + `mtime_ns`) checked before and after each write.

## Limitations

- Tag length is capped at 63 characters after sanitization.
- Inventory total is `None` for Essentials / online libraries (shown as *not counted*).

## Sponsors

BATM was developed during an internship and is sponsored by **B-Water Studios** — https://b-waterstudios.com/

