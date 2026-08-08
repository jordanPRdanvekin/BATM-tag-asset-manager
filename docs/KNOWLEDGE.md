# Knowledge Dictionary (schema)

Resource: `resources/autotag_knowledge.json`.

```json
{
  "schema_version": 1,
  "groups": [
    {
      "tag": "Wood",
      "words": ["oak", "maple", "pine", "plywood", "walnut"]
    }
  ]
}
```

- `tag` is the canonical category Tag BATM adds.
- `words` are matched (case-insensitively) against a word pool built from the
  asset name tokens, material names, object types/names and geometry node
  names.
- Matching is deterministic: groups are checked in declaration order and a
  group only matches once per asset; at most 6 categories per asset.
- The same groups feed the Sanitizer's **Merge Synonyms**: when enabled, every
  word is mapped to its group Tag and the Sanitizer canonicalizes them before
  deduplication.

Keep the file valid JSON — a broken or missing file simply disables
Knowledge-based tagging (never crashes the add-on).