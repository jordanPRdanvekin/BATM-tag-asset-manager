"""Deterministic tag parsing, cleaning and normalization tests (no Blender)."""

import sys

from _setup import ADDON_ROOT  # noqa: F401  (installs the batm package)

from batm.core.sanitizer import build_synonym_map, default_options, sanitize_tags

opt = default_options()

# Default behavior matches the historic preset.
assert sanitize_tags("Red,Red Character arm arm1 arm2") == (
    ["Red", "Red Character Arm Arm1 Arm2"],
    [],
)
assert sanitize_tags("Comedy Island") == (["Comedy Island"], [])

# Casing modes.
opt["casing"] = "SNAKE"
assert sanitize_tags("Oak Tree,rock_01", opt) == (["oak_tree", "rock_01"], [])
opt["casing"] = "UPPER"
assert sanitize_tags("oak tree", opt) == (["OAK TREE"], [])
opt["casing"] = "KEBAB"
assert sanitize_tags("Oak Tree", opt) == (["oak-tree"], [])
opt["casing"] = "PASCAL"
assert sanitize_tags("oak tree", opt) == (["OakTree"], [])
opt["casing"] = "CAMEL"
assert sanitize_tags("oak tree", opt) == (["oakTree"], [])
opt["casing"] = "LOWER"
assert sanitize_tags("Oak Tree", opt) == (["oak tree"], [])
opt["casing"] = "NONE"
assert sanitize_tags("oak_Tree", opt) == (["oak_Tree"], [])

# Separator modes.
base = default_options()
base["casing"] = "NONE"
assert sanitize_tags("a|b|c", {**base, "separators": "PIPE"}) == (["a", "b", "c"], [])
assert sanitize_tags("a b c", {**base, "separators": "SPACE"}) == (["a", "b", "c"], [])
assert sanitize_tags("a;b", base) == (["a", "b"], [])

# Trailing/isolated numbers.
base["remove_isolated_numbers"] = True
assert sanitize_tags("Rock_01,42", base) == (["Rock"], [])
assert sanitize_tags("Rock 2021", base) == (["Rock"], [])

# Synonyms merge.
base["merge_synonyms"] = True
base["synonyms"] = {"wolf": "Animal"}
assert sanitize_tags("Wolf", base) == (["Animal"], [])

# Max length truncation.
short = default_options()
short["max_length"] = 5
assert sanitize_tags("abcdefgh", short) == (["Abcde"], [])

# Blacklist.
bl = default_options()
bl["blacklist"] = ["junk"]
assert sanitize_tags("Junk,Keep", bl) == (["Keep"], [])

# Per-asset cap avoids tag flooding without blocking. Keeps the first N
# Tags in input (priority) order, then sorts the result.
cap = default_options()
cap["max_tags"] = 4
cleaned, errors = sanitize_tags("Zebra,Alpha,Bravo,Delta,Charlie,Extra", cap)
assert len(cleaned) == 4 and errors == [], (cleaned, errors)
assert cleaned == ["Alpha", "Bravo", "Delta", "Zebra"], cleaned
# 0 (the default) keeps everything.
assert len(sanitize_tags("a,b,c,d,e,f", default_options())[0]) == 6

# Synonym map from knowledge groups.
synonyms = build_synonym_map([{"tag": "Wood", "words": ["oak", "maple", "pine"]}])
assert synonyms["oak"] == "Wood"

print("SANITIZER TESTS OK")