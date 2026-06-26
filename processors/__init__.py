"""
Public exports for the processors package.

The category processor is now a function (process_categories) not a class.
Tag and Location are still class-based.
"""

from processors.category import process_categories
from processors.tag import TagProcessor
from processors.location import LocationProcessor

__all__ = ["process_categories", "TagProcessor", "LocationProcessor"]
