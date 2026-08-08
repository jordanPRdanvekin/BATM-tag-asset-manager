# AutoTag Rules (schema v1)

Declarative JSON rules file: `resources/autotag_rules.json`.

```json
{
  "schema_version": 1,
  "rules": [
    {
      "rule_id": "uuid",
      "name": "Rigged assets",
      "enabled": true,
      "priority": 40,
      "match_field": "constraint_types",
      "match_mode": "ANY",
      "match_values": ["TRACK_TO"],
      "add_tags": ["Rigged"]
    }
  ]
}
```

## Match fields

| Field                 | Facts                            |
| --------------------- | -------------------------------- |
| `name_tokens`         | Words of the datablock name      |
| `id_type`             | OBJECT / MATERIAL / COLLECTION … |
| `library_reference`   | Owning Asset Library name        |
| `library_tag`         | Normalized library name Tag      |
| `path_tokens`         | Tokens of the .blend path        |
| `object_names`        | First-level children names       |
| `object_types`        | First-level children types       |
| `modifier_types`      | Modifier types on the data       |
| `material_names`      | Assigned material names          |
| `rig_types`           | Rig structure types              |
| `geometry_node_names` | Geometry node group names        |
| `bone_names`          | Bone names inside armatures      |
| `constraint_types`    | Constraint types (TRACK_TO…)     |
| `parent_names`        | Parent / family object names     |
| `hierarchy_depth`     | Max hierarchy depth (int)        |

`match_mode`: `ANY` (any value matches), `ALL` (every value must match),
`EXACT` (fact set equals the values). Values are matched case-insensitively; a
rule matches when its fields exist in the asset's extracted facts.

## Priorities

Rules run in ascending `priority` order; a Tag the Sanitizer caps can be
cleaned before the Preview is built. Output tags pass through the Sanitizer
like any other input.