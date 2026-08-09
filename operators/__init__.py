"""BATM operator package."""

from .diagnostics import CLASSES as DIAGNOSTIC_CLASSES
from .manual import CLASSES as MANUAL_CLASSES
from .rules import CLASSES as RULE_CLASSES
from .run import CLASSES as RUN_CLASSES

CLASSES = MANUAL_CLASSES + RULE_CLASSES + RUN_CLASSES + DIAGNOSTIC_CLASSES
