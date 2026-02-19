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

"""Language model inference compatibility layer.

New code should import from loop_core.vendor.langextract.core.base_model instead.
"""

from __future__ import annotations

import enum
import warnings


class InferenceType(enum.Enum):
  """Enum for inference types - kept for backward compatibility."""

  ITERATIVE = "iterative"
  MULTIPROCESS = "multiprocess"


def __getattr__(name: str):
  """Forward attribute access to core modules for backward compatibility."""
  moved = {
      "BaseLanguageModel": ("loop_core.vendor.langextract.core.base_model", "BaseLanguageModel"),
      "ScoredOutput": ("loop_core.vendor.langextract.core.types", "ScoredOutput"),
      "InferenceOutputError": (
          "loop_core.vendor.langextract.core.exceptions",
          "InferenceOutputError",
      ),
      "GeminiLanguageModel": (
          "loop_core.vendor.langextract.providers.gemini",
          "GeminiLanguageModel",
      ),
  }
  if name in moved:
    mod, attr = moved[name]
    warnings.warn(
        f"`langextract.inference.{name}` is deprecated; use `{mod}.{attr}` instead.",
        FutureWarning,
        stacklevel=2,
    )
    module = __import__(mod, fromlist=[attr])
    return getattr(module, attr)
  raise AttributeError(name)
