"""Makes ``custom_components/optoma_link`` importable as ``optoma_link``.

The integration isn't installed as a package -- Home Assistant loads it
straight out of ``custom_components/`` at runtime -- so tests need the same
path on ``sys.path`` that HA would give it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))
