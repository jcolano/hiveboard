# Copyright 2025 Google LLC.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""LangExtract: Extract structured information from text with LLMs.

Vendored copy for loopCore. Visualization and google-cloud-storage removed.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any, Dict

from loop_core.vendor.langextract.extraction import extract as extract_func

_PKG = "loop_core.vendor.langextract"

__all__ = [
    "extract",
    "annotation",
    "data",
    "providers",
    "schema",
    "inference",
    "factory",
    "resolver",
    "prompting",
    "io",
    "exceptions",
    "core",
    "plugins",
]

_CACHE: Dict[str, Any] = {}


def extract(*args: Any, **kwargs: Any):
  """Top-level API: lx.extract(...)."""
  return extract_func(*args, **kwargs)


# PEP 562 lazy loading
_LAZY_MODULES = {
    "annotation": f"{_PKG}.annotation",
    "chunking": f"{_PKG}.chunking",
    "data": f"{_PKG}.data",
    "data_lib": f"{_PKG}.data_lib",
    "debug_utils": f"{_PKG}.core.debug_utils",
    "exceptions": f"{_PKG}.exceptions",
    "factory": f"{_PKG}.factory",
    "inference": f"{_PKG}.inference",
    "io": f"{_PKG}.io",
    "progress": f"{_PKG}.progress",
    "prompting": f"{_PKG}.prompting",
    "providers": f"{_PKG}.providers",
    "resolver": f"{_PKG}.resolver",
    "schema": f"{_PKG}.schema",
    "tokenizer": f"{_PKG}.tokenizer",
    "core": f"{_PKG}.core",
    "plugins": f"{_PKG}.plugins",
    "registry": f"{_PKG}.registry",
}


def __getattr__(name: str) -> Any:
  if name in _CACHE:
    return _CACHE[name]
  modpath = _LAZY_MODULES.get(name)
  if modpath is None:
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
  module = importlib.import_module(modpath)
  sys.modules[f"{__name__}.{name}"] = module
  setattr(sys.modules[__name__], name, module)
  _CACHE[name] = module
  return module


def __dir__():
  return sorted(__all__)
