"""Extension tool loader -- discovers BaseTool subclasses from a drop-in folder.

Extensions are single .py files (or packages with __init__.py) placed in
``data/loopCore/EXTENSIONS/``.  Each file defines one or more BaseTool subclasses
that are automatically discovered, instantiated, and registered alongside built-in
tools at startup.

Discovery rules:
- Files starting with ``_`` are ignored (quick way to disable an extension).
- Packages (directories with ``__init__.py``) are also supported.
- Only concrete BaseTool subclasses defined *in the loaded module* are picked up
  (not re-exported base classes or abstract intermediaries).

Error handling:
- A broken extension (syntax error, missing dependency, __init__ crash) logs a
  warning and is skipped -- it never prevents other extensions from loading.
"""

import importlib.util
import inspect
import sys
from pathlib import Path
from typing import List

from .base import BaseTool


def load_extensions(extensions_dir: str) -> List[BaseTool]:
    """Scan directory for .py files/packages, find BaseTool subclasses, instantiate."""
    tools: List[BaseTool] = []
    ext_path = Path(extensions_dir)
    if not ext_path.is_dir():
        return tools
    for entry in sorted(ext_path.iterdir()):
        if entry.is_file() and entry.suffix == ".py" and not entry.name.startswith("_"):
            tools.extend(_load_module(entry))
        elif (
            entry.is_dir()
            and not entry.name.startswith("_")
            and (entry / "__init__.py").exists()
        ):
            tools.extend(_load_module(entry / "__init__.py", package_name=entry.name))
    return tools


def _load_module(file_path: Path, package_name: str = None) -> List[BaseTool]:
    """Import a .py file, find and instantiate BaseTool subclasses."""
    mod_name = f"loopcore_ext.{package_name or file_path.stem}"
    tools: List[BaseTool] = []
    try:
        spec = importlib.util.spec_from_file_location(mod_name, str(file_path))
        if not spec or not spec.loader:
            return tools
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseTool)
                and obj is not BaseTool
                and not inspect.isabstract(obj)
                and obj.__module__ == module.__name__
            ):
                try:
                    tools.append(obj())
                except Exception as e:
                    print(f"[WARN] Extension {file_path.name}: failed to instantiate {name}: {e}")
    except Exception as e:
        print(f"[WARN] Extension {file_path.name}: failed to load: {e}")
    return tools
