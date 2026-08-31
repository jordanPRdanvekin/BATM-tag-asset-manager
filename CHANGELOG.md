# Changelog

## 3.0.0 — Blender 5.2 LTS Extension

- Rebuilt BATM as a Blender 5.2 LTS Extension (`blender_manifest.toml`, `bl_info` 5.2.0, `bpy` Asset Browser APIs).
- Added frozen selection identities using library, path, ID type and datablock name.
- Added conservative rule-based AutoTag and an in-Blender rule editor (19 match fields, 23 bundled rules).
- Added queue-only manual Add, Remove, Replace operations.
- Added editable, filtered and paginated Before/After Review.
- Added sanitization with strict 63-character validation.
- Added checksummed Tag backups, automatic rollback and startup recovery.
- Added static background workers with versioned JSON IPC and post-save verification.
- Added adaptive 1–4 process scheduling without Python threads.
- Added asynchronous writable-library inventory, structured logs and diagnostics.
- Audited and hardened for 5.2 LTS: validated `blender_manifest.toml` (`website` → https://b-waterstudios.com, `blender_version_min` 5.2.0), fixed `ADDON_ID`, corrected `pyproject.toml` tooling paths, fixed `library_path` → `path_tokens` rule field, hardened `BATM_BASE_DIR` and worker IPC path traversal checks, fixed sanitizer `max_tags` truncation order (slice after sort) and generalized the BOTH compound handling, fixed registration `F8` reload and state-machine transitions, made metrics/UI `grid_flow(columns=0)` native and responsive, and synchronized README/SSOT/docs with the shipped code.

> BATM was developed during an internship and is sponsored by B-Water Studios — https://b-waterstudios.com/  
> *Desarrollado durante unas prácticas y patrocinado por B-Water Studios.*
