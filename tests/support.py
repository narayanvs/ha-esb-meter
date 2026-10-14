"""Load pure integration modules without importing HA's integration entrypoint."""
import importlib
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1] / "custom_components/esb_meter"
package = types.ModuleType("esb_test_package")
package.__path__ = [str(ROOT)]
sys.modules.setdefault("esb_test_package", package)


def module(name):
    return importlib.import_module("esb_test_package." + name)
