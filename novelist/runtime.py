"""Locations shared by development and self-contained application builds."""
import os
from pathlib import Path

RESOURCE_ROOT = Path(os.environ.get("NOVELIST_RESOURCE_ROOT", Path(__file__).resolve().parent.parent))
DATA_ROOT = Path(os.environ.get("NOVELIST_DATA_ROOT", RESOURCE_ROOT))
