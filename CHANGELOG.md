# Changelog

## v3.0.0 (current)

- **AutoTag fix — animals no longer tagged "Human".** Two root causes repaired:
  (1) glued name compounds (`foxcub`) matched nothing under exact-token catalog
  matching, hiding fauna evidence and letting contextual age terms (`child`,
  `baby`, `kid`) fall back to their human default — the catalog matcher now
  recovers compound components with the same prudent segmenter used by Cleanup;
  (2) when fauna evidence exists, the whole Human domain is suppressed, so an
  animal also named a "character", "hero" or "beast" is never tagged Human.
- **Split Compound Words fix.** Tokens joined by `_` / `-` / spaces were skipped
  by the segmenter (only contiguous alphanumeric tokens were segmented), and the
  lexicon lacked common components — `mat_foxcub_eyecornea` stayed intact. Each
  separator-joined piece is now decomposed on its own, so it yields `Mat, Fox,
  Cub, Eye, Cornea` (plus the combined forms in "Both" mode). Pieces that are
  already proper words stay combined (`fire_force` → "Fire Force", unchanged),
  and self-preserving thesaurus entries (`promesh`) never count as splits.
  Lexicon extended with common words (`mat`, `eye`, `cornea`, animal anatomy)
  and the catalog's contextual terms (`cub`, `pup`, ...).
- **Filter + pagination everywhere Tags are listed.** The Manual Tag Editor and
  the Review last-step Final Tags preview now match the Add step: first /
  previous / next / last page buttons, 10-per-page default switchable to 100,
  and the free-text + character-length filters applied across the whole set, so
  every Tag that will be added can be reviewed before accepting anything.
  Filtering logic is shared from `core/session` (`filter_tag_list`,
  `filtered_final_tags`), which also fixes Select All / pagination ignoring the
  length filter in the Manual Tag Editor.
- **Edit proposed Tags (Review → Add step):** double-click any proposed Tag (or
  click the pencil) to rewrite it on every asset that would add it. Implemented as
  two Add-step operations (`CANCEL_ADD` + verbatim `ADD`), so it works even with
  the Remove step OFF and never touches existing Tags.
- **Tag length filter:** a `Length` field next to the existing free-text ("manual")
  search filters Tags by exact number of letters/digits (e.g. `2` → only 2-char Tags).
  Available in the Manual Tag Editor and in the Review → Add step. Shared helper
  `core/sanitizer.tag_char_length()`.
- **Review → Add step pagination:** the proposed Tags list is now paginated (10 per
  page by default, switchable to 100) with first / previous / next / last navigation,
  so every Tag that will be added can be reviewed before accepting. Filters (manual
  search + length) apply to the whole set across all pages; double-click / pencil /
  X edit actions work per page. Shared aggregation/filter logic lives in
  `core/session.filtered_added_tags()`.
- **Version consolidated to 3.0.0.** BATM ships under a single definitive production
  version (`BATM_VERSION = (3, 0, 0)`, manifest `version = "3.0.0"`); the `3.2.0`
  entry below is preserved verbatim as the development history that produced this
  release.
- **Release cleanup (behavior-preserving):**
  - Removed the orphaned `levenshtein()` helper from `engine/segmenter.py` (its split path had already been removed and no production caller remained) together with the self-referential asserts in `tests/test_segmenter.py`.
  - Removed the residual `BATM_3.2` legacy module-name fallback from the AddonPreferences lookup (`adapters/blender_assets.py`); the stable extension id `batch_asset_tag_manager` is the only match now.
  - Release packaging hardened: `tools/` and `dist/` are now excluded from the installable ZIP, and the manifest `paths_exclude_pattern` is aligned with the builder, so no development tooling, tests, docs, CI config or generated artifacts ship with the add-on.

## v3.2.0

- BATM identity consolidated to **v3.2** (Blender target **5.2.x**). The `4.x` labels used earlier in this workspace were a labeling error; the full change history below documents the work that produced 3.2 and is preserved verbatim.
- Rebuilt BATM as a Blender 5.2 Extension.
- **Lifecycle hardening:** `register()` now resets the transient session state via a new `SessionController.reset()` and `unregister()` cancels any active scheduler, so an F8 reload or disable/enable never leaves a phantom Review, stale snapshots or a dangling worker behind. No functional behavior changes.
- **Bugfix (analysis with external files):** the batched `ANALYZE` worker crashed with `KeyError: 'status_path'` on every asset, so any run over writable library .blend files failed with "Analysis failed; open Diagnostics". Root cause: `worker/batm_worker.py` built the per-file `sub` passed to `process_assets` without the `status_path` key that `process_assets` writes unconditionally. The fix includes `status_path` in the sub-request; worker status/timing now works as intended.

- **Add-step exclusion in the Review (`core/reducer.py`, `operators/run.py`).**
  - The "X" on a proposed Tag in the **Add** step was implemented as a `REMOVE`
    operation, so it only took effect when the **Remove** step was ON — and it
    conflated "do not add" with "remove existing". It is now a first-class
    **ADD exclusion** (`CANCEL_ADD`): it stops the proposed/auto Tag from being
    added (so AutoTag cannot re-add it), it is effective even when the Remove
    step is OFF, and it never removes a Tag that already exists on an asset.
  - The reducer applies the exclusion on the sanitized output (so the value as
    shown in the Review, e.g. `Oak Tree` from a split `oaktree`, is matched),
    and keeps any pre-existing instance of that Tag untouched.
  - Added `tests/test_add_exclusion.py` covering: exclusion blocks a proposed
    Tag; effective with Remove OFF; does not touch existing Tags; no-op when Add
    is OFF; idempotent re-scan.

- **AutoTag / Cleanup segmentation: contiguous tokens never gain an invented boundary (`engine/segmenter.py`).**
  - The Levenshtein "corrected-piece" split path was **removed**. A contiguous token may only split when a full **exact-lexicon** decomposition exists (`_known_split`), i.e. every component is a known word (`oaktree` ⇒ `oak` + `tree`). A token that would need a *corrected* (near-miss, non-exact) component to split is preserved intact — `fireforce` stays `Fireforce`, `apron`/`klawitter`/proper nouns stay whole — instead of a dictionary-inferred frontier being fabricated. This both fixes the over-segmentation root cause and removes the expensive per-token Levenshtein DP that froze the main-thread Preview on large libraries. No per-word exceptions were added.
  - Explicit separators remain first-class evidence: `fire_force`, `fire-force` and `fire force` all collapse to the single Tag `Fire Force` via the sanitizer's separator/casing layer (Token boundary ≠ final Tag boundary). Casing and segmentation stay independent.
  - **Reconciliation/repair:** the reducer (`compile_states`) computes a correct ADD/REMOVE diff when an existing Tag is normalized/merged (`Wolf` → `Animal`, `oak_tree` → `Oak Tree`), and a second scan after apply yields **NO CHANGE** (idempotent).

- **Centralized diagnostics:** new `engine/logging.capture_exception()` records every recoverable failure with full evidence (add-on/Blender version, module/file/function/line, exception, traceback, active operation and phase) into a timestamped `diagnostic_*.json`. It never raises and falls back to an alternate file then stderr, so error handling can never cascade. Wired into the run (analysis/execute/current-file/rollback) and restore error paths.

- **Bugfix (Windows, analysis progress):** a `PermissionError` / `[WinError 5] Access Denied`
  on `os.replace` of the worker `status_*.json` aborted analysis. On Windows `os.replace`
  fails while the main Blender process has the target open (no `FILE_SHARE_DELETE`), and the
  worker now writes status per asset (after the `status_path` fix). `atomic_json_write`
  (`adapters/storage.py`) and the worker's `atomic_write` now retry briefly and, as a
  Windows last resort, fall back to a non-atomic direct write so a status/result update never
  aborts a run; readers already tolerate partial JSON.

- **Bugfix (asset identity):** `find_local_id` (`adapters/blender_assets.py`) and the worker's
  `find_id` matched datablocks only by `(id_type, name)` across `bpy.data.all_ids`, which also
  contains linked-library datablocks. In a file that links another library, a homonymous linked
  asset could be matched and edited instead of the file's own asset. Both now prefer the
  non-linked datablock owned by the file being edited (`datablock.library is None`), keeping a
  fallback to the previous behavior so linked-only assets still resolve.
- **Bugfix (rollback completeness):** `_begin_rollback` restored only `completed` workers. A
  started `APPLY` worker that failed after saving (or was killed right after the save) is
  recorded in `failed`, leaving its file modified while `_finish_restored` deleted the backup
  and reported "restore verified" (data loss / false success). Rollback now restores every
  started job (`completed` + `failed`); the `RESTORE` worker no-ops files that were not touched
  and raises (backup retained) instead of overwriting files that match neither state.



- Added frozen selection identities using library, path, ID type and datablock name.
- Added conservative rule-based AutoTag and an in-Blender rule editor.
- Added queue-only manual Add, Remove, Replace and Merge operations.
- Added editable, filtered and paginated Before/After Review.
- Added Legacy Clean sanitization with strict 63-character validation.
- Added checksummed Tag backups, automatic rollback and startup recovery.
- Added static background workers with versioned JSON IPC and post-save verification.
- Added adaptive 1-4 process scheduling without Python threads.
- Added asynchronous writable-library inventory, structured logs and diagnostics.
- Added concept catalog (resources/taxonomy.json), MetaTags engine and catalog control in Preferences.
- Added prudent word segmentation (Split Compound Words) with deterministic fixups/thesaurus.
- Manual Replace by selection (no typing of the source).
- Native progress; removed the custom panel progress bar.
- Guided 4-step Review with Asset Type scope confirmation.
- Hardened rule editing (match_field guard) and stripped UTF-8 BOM.
- **Phase 2 (UI hierarchy + filter consolidation):**
  - Strict layout order: METRICS -> ASSET TYPE FILTER -> RUN BATM -> MANUAL -> SETTINGS -> DIAGNOSTICS.
  - RUN BATM is now always visible, wide, large and never collapsible (removed `run_expanded`).
  - `View` and `Asset Type Filter` consolidated into one control in the upper context zone; it is the single source of truth for AutoTag and the Manual editor.
  - Added Object-subtype filters (Empty, Mesh, Camera, Light); external assets expose only the Object id_type, so a subtype filter cannot select them at analyze time (documented limitation).
- **De-overengineering sweep (review):**
  - Consolidated duplicate Asset Browser refresh into the shared adapter helper (`refresh_asset_browser`); removed two unexposed refresh operators (`refresh_inventory`, `refresh_browser`) - the single "Refresh" action remains.
  - Object-subtype Filter now resolves deep facts lazily, only when a subtype filter is active, so Manual actions no longer run the heavy extractor for ALL / id_type filters.
  - Filter matcher uses the documented id_type/subtype vocabularies (no dead constant) and rejects unknown values deterministically.
- **UI / Preview / Manual refinement:**
  - Manual Tag Editor: pending operations can now be cancelled individually (`batm.pending_cancel`) or per queue (`batm.pending_clear_kind` for Add / Remove), alongside the existing "Clear All Pending".
  - Preview Step 1 (Tags to Add) and Step 2 (Tags to Remove) let you cancel individual proposed Tags before confirming.
  - Preview Step 4 (Asset Fine-Tune) now opens with an aggregated **Final Tags** preview of every Tag that will be applied, with `count/total` coverage across the selection.
  - Registered the previously unregistered `batm.review_remove_added` / `batm.review_remove_tag_value` operators so the Preview per-Tag cancel buttons are active.
  - Added a descriptive `bl_description` to every operator that lacked one; cleaned the Review/step label separators (no stray characters; no internal audit codes exposed).
  - Removed the stale `tests/test_asset_type_filter.py` (the Asset Type Filter is no longer present in production, per the requirement to remove it from the interface).
- **Stage coherence update (Add / Remove / Cleanup / Last Step):**
  - **Version:** consolidated to Addon **3.2** / Blender **5.2.x** (see note above; the prior `v4.x` labels were corrected).
  - **Compound Words (SPLIT / COMBINED / BOTH):** the combined representation is now the semantically correct unit derived from the split evidence - not always the raw token and not always `"-".join(parts)`. A curated thesaurus split preserves the original token (`promeshchild`); a lexical-discovery split rejoins its components through casing/separators (`oaktree` -> `oak tree`). The combined form is never fed back into decomposition, so a validated representation cannot be re-split. `promesh` stays a single unit (no over-split without evidence) and `promeshchild` segments with evidence. The curated examples moved out of the engine into `resources/compound_splits.json`; the segmentation engine is fully generic (no hardcoded token branches).
  - **Preview reactive toggles:** toggling any of Add / Remove / Cleanup / Last Step now updates the Review in real time through a single guarded recompile path (`_live_recompile` with a reentrancy guard). Still no writes, no backup, no execute and no recursive callbacks.
  - **Cleanup terminology:** visible labels updated - `Sanitize Rules` -> `Cleanup Rules`, `Sanitizer` -> `Cleanup`; the Review-step operator description now reads "Add, Remove, Cleanup and Last Step".
  - **Duplicate property removed:** the redundant first `sanitize_recombine` declaration (overridden by the effective second one) was deleted; a single contract remains.
  - **Tests:** added `tests/test_stage_pipeline.py` (16 combinations, `0000` -> NO-OP, active-only Reducer, UI defaults, Disable All -> no-op) and updated `tests/test_steps_recombine.py` to the compound contract (`promesh` / `promeshchild` / `oaktree` across SPLIT / COMBINED / BOTH, no over-split, no re-segmentation, DOUBLE_HYPHEN unaffected). The full offline suite is green.
  - **Docs:** added `docs/GRAPHS.md` (global flow, stage control, data flow, Cleanup, compound words, reactive Preview, error/recovery, transactional execution and the development flow) and updated the README to the Add / Remove / Cleanup / Last Step model with a link to the graphs.
- **Performance / hang repair (large selections: 2000+ assets):**
  - **Root cause:** the segmenter lexicon was rebuilt for **every** `sanitize_tags` call - reloading Knowledge + taxonomy JSON and rebuilding the word set. With **Split Compound Words ON** (the default), Review construction (`compile_states`) and every live Preview toggle recomputed that lexicon once per asset, so the main thread blocked / froze Blender (hang or silent close, no report) as the selection grew. `_correct_split` also re-sorted the whole lexicon for every compound token.
  - **Fix:** `_segment_lexicon` now returns a **cached** `(words, pre-sorted)` pair built once per session, and `segmenter.decompose` accepts the pre-sorted list so no per-token sorting happens. Behavior is unchanged (the lexicon was already built from the same static Knowledge + taxonomy bundle).
  - **Validation (offline):** full suite green; a 3000-asset compile with Split Compound Words ON now completes in ~0.19 s (first run includes the one-time lexicon build) instead of scaling with `assets x lexicon rebuild`. Runtime in Blender 5.2 still to be confirmed.
  - **Files:** `core/sanitizer.py`, `engine/segmenter.py`.
