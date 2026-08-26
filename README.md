# BATM — Batch Asset Tag Manager

**BATM v3.0.0** · Blender 5.2+


BATM (Batch Asset Tag Manager) is a professional Blender 5.2+ extension for
managing Asset Browser tags at library scale. It analyzes selected assets in
background Blender workers, proposes Tag changes through an editable Review,
backs up every change, and applies them with fingerprint verification —
including automatic rollback if anything fails.

No Tag is ever written before you review and approve the proposed changes.

## Workflow

1. **Select assets** in the Asset Browser sidebar, then **Run BATM**.
2. BATM fingerprints the files, extracts structural facts and runs the
   AutoTag engine, the bundled Knowledge dictionary and the concept catalog.
3. **Review**: the four-stage preview — **Add**, **Remove**, **Cleanup** (the Sanitizer) and **Last Step** (per-asset fine-tune), each with its own ON/OFF toggle.
   **Enable All / Disable All** switch only these four stages. See the **Final Tags**
   preview with `x/x` coverage, and fine-tune individual assets before enabling them.
4. **Confirm and Apply** creates a verified backup and runs worker processes
   to write the tags. Every written asset is re-read and verified.
5. **Diagnostics & Logs** shows summaries, the event log, recoverable backups
   and operational state, and lets you restore the latest backup.

## Modes

- **AutoTag**: deterministic rules (see `docs/AUTOTAG_RULES.md`) over facts
  extracted without opening .blend files more than necessary (read-only reads).
- **Knowledge dictionary**: bundled category Tags matched by words from names
  and materials (`resources/autotag_knowledge.json`).
- **Concept catalog**: `resources/taxonomy.json` maps domain → category → word
  and emits conceptual Tags with a traceable reason.
- **Manual Tag Editor**: unified Tag list for the current selection with
  Add / Remove / Replace queued as operations, and a **Pending** section to
  cancel individual or whole queues before applying.
- **Cleanup** (the Sanitizer): normalizes and repairs proposed Tags — casing,
  separators, deduplication, blacklist, max Tag length (63) and synonym merging.

## Stages

The pipeline is **Add → Remove → Cleanup → Last Step** (equivalents of Tags to
Add, Tags to Remove, Sanitizer and Asset Fine Tuning). Each stage is
independently toggleable before apply; the reducer only consumes operations
from **active** stages, so an OFF stage never touches the plan or the diff. The
full flow, stage, data, Cleanup, compound-word, reactive-Preview, error and
transactional graphs live in [`docs/GRAPHS.md`](docs/GRAPHS.md), which mirrors
the real code.


## Safety

- Every run creates a checksummed, timestamped backup manifest.
- Workers are killed by the watchdog deadline if they hang.
- Applying verifies fingerprints before and after each file write.
- Cancel mid-execution rolls back every file already written and verifies.
- No Python threading touches `bpy`; all background work uses subprocesses
  and is polled from the Blender main thread.

## Configuration (add-on preferences)

- `Max Workers`: 1–4 parallel background processes.
- `Worker Timeout`: seconds per request before a worker is killed.
- `Review Page Size`, `Log Retention Days / Runs`.
- AutoTag Settings: catalog level (Concepts / Full) and Cross-Tagging.

## UI hierarchy

The Asset Browser sidebar follows a strict, predictable order:

```
METRICS       (collapsible; active library, asset count, selection, catalog)
RUN BATM      (always visible, wide, large, never collapsible)
TAG EDITOR    (collapsible; add / remove / replace / clone Tags)
SETTINGS      (collapsible)
  ├─ Manual & Dictionary   (AutoTag rules, import / export, bundled reset)
  ├─ Sanitizer             (Cleanup Rules: casing, separators, segmentation)
  └─ Technical Details     (diagnostics, exports, refresh)
RECOVERY      (always visible only when backups or errors exist)
```

`Run BATM` is the single entry point for every workflow. Metrics sit just above
it and collapse so the main action always stays dominant; the Tag Editor lets
you queue Add / Remove / Replace / Clone in one or two clicks; Settings groups
Manual & Dictionary, the Sanitizer and Technical Details so power-user options
never crowd the normal interface. Only native, public Blender APIs are used; no
ephemeral filter state is kept for the Asset Browser.

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
python tests/test_stage_pipeline.py
python tests/test_steps_recombine.py
python tests/test_segmenter.py
python tests/test_taxonomy.py
python tests/test_logging.py
python tests/test_fingerprint.py
python tests/test_backup.py
python tests/test_false_success.py


```

All tests are pytest- and unittest-compatible plain asserts so they run in CI
without external dependencies. See `docs/` for rules and knowledge schemas.

## Patch Notes

### 3.0.0 (current)

**Changed**

- Version relabeled to **3.0.0** (no functional changes); the `3.2.0` entry
  below is kept as the development history that produced this release.

**Cleaned (behavior-preserving)**

- Removed the orphaned `levenshtein()` helper (its split path was already gone)
  and the residual `BATM_3.2` legacy module-name fallback.
- The release ZIP no longer ships development tooling (`tools/`), build output
  (`dist/`), tests, docs or CI config; manifest exclusions are aligned with the
  builder.

### 3.2.0

**Fixed / corrected**

- Lifecycle hardening: `register()` resets transient session state and
  `unregister()` cancels any active scheduler, so an F8 reload or
  disable/enable never leaves a phantom Review, stale snapshots or a dangling
  worker behind (no functional change).
- Manual operator registration: the Review per-tag cancel buttons
  (`review_remove_added`, `review_remove_tag_value`) were defined but never
  registered, so they were dead buttons; they are now active.
- Centralized diagnostics: `engine/logging.capture_exception()` persists every
  recoverable failure with add-on/Blender version, module/file/function/line,
  traceback, active operation and phase into `diagnostic_*.json`; it never
  raises and falls back to an alternate file then stderr, so error handling can
  never cascade. Wired into the run (analysis/execute/current-file/rollback) and
  restore error paths.
- Bugfix (analysis with external files): the batched `ANALYZE` worker crashed with
  `KeyError: 'status_path'` on every asset, so any run over writable library .blend
  files failed with "Analysis failed; open Diagnostics". Fixed by including
  `status_path` in the per-file sub-request (`worker/batm_worker.py`).
- Bugfix (asset identity): `find_local_id` and the worker's `find_id` matched
  datablocks by `(id_type, name)` across `bpy.data.all_ids`, including linked
  library datablocks, so a homonymous linked asset could be edited instead of
  the file's own asset. Both now prefer the non-linked datablock (`library is
  None`), with a fallback for linked-only assets.
- Bugfix (rollback completeness): rollback now restores every started worker
  (`completed` + `failed`), so a file saved but whose worker failed after the
  save is restored too instead of being left modified with the backup deleted.
- Grease Pencil handled as its real ID type (`GREASE_PENCIL`) instead of a
  mismatched enum value.
- Duplicate Asset Browser refresh logic consolidated into a single shared
  helper; two unexposed refresh operators removed (only `Refresh` remains).
- Dead code removed (unused filter constant, unused helper), and deep fact
  extraction is now lazy so it only runs when strictly needed.
- Removed the stale test that referenced the no-longer-present Asset Type
  Filter.
- UI text cleaned (no stray characters) and a descriptive `bl_description`
  added to every operator that lacked one; no internal audit codes are shown.

**Changed / added**

- UI order is now Metrics → Run BATM → Manual → Settings → Diagnostics.
- Run BATM is always visible and never collapsible; all other sections are
  collapsible.
- The View / Asset Type filters were reconciled with the Asset Browser and
  ultimately removed from the interface to avoid desynchronization.
- Manual Tag Editor: a Pending section lets you cancel a single pending
  operation or clear the whole Add / Remove queue before applying.
- Review Step 4 shows an aggregated **Final Tags** preview (every Tag that will
  be applied) with `x/x` coverage across the selection.
- Concept catalog, MetaTags, prudent compound-word segmentation, catalog scope
  and cross-tagging, and Manual Replace by selection were introduced.

### 3.2.0 (foundation)

- Rebuilt BATM as a Blender 5.2 Extension with a transactional workflow
  (Analyze → Sanitize → Review → Backup → Apply → Verify); no instant writes.
- Frozen asset identities using library, path, ID type and datablock name —
  never just the asset name.
- Conservative, rule-based AutoTag with an in-Blender rule editor.
- Queue-only Manual Add / Remove / Replace / Merge operations.
- Editable, filtered and paginated Before/After Review.
- Sanitizer with casing, separators, deduplication, 63-character validation,
  blacklist, synonym merge and a per-asset cap.
- Checksummed Tag backups, verified automatic rollback and startup recovery.
- Static background workers with versioned JSON IPC and post-save verification.
- Adaptive 1–4 process scheduling without Python threads.
- Asynchronous writable-library inventory, structured logs and diagnostics.
