"""Import this FIRST in every test file: points DataDistill at an empty temporary library (database, data folder,
outputs) so that no test can touch the real one. Must run before datadistill.config is imported."""
import os
import sys
import tempfile
from pathlib import Path

if "datadistill.config" not in sys.modules:
    _tmp = Path(tempfile.mkdtemp(prefix="dd_test_"))
    os.environ["DD_DATA_DIR"] = str(_tmp / "data")
    os.environ["DD_OUTPUT_DIR"] = str(_tmp / "output")
