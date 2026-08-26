"""BATM operator package."""

from .diagnostics import CLASSES as DIAGNOSTIC_CLASSES
from .manual import CLASSES as MANUAL_CLASSES
from .rules import CLASSES as RULE_CLASSES
from .run import CLASSES as RUN_CLASSES
from .taxonomy import CLASSES as TAXONOMY_CLASSES

CLASSES = MANUAL_CLASSES + RULE_CLASSES + RUN_CLASSES + TAXONOMY_CLASSES + DIAGNOSTIC_CLASSES
