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

"""Schema compatibility layer.

New code should import from loop_core.vendor.langextract.core.schema instead.
"""

from __future__ import annotations

import warnings


def __getattr__(name: str):
  """Handle imports with appropriate warnings."""
  core_items = {
      "BaseSchema": ("loop_core.vendor.langextract.core.schema", "BaseSchema"),
      "Constraint": ("loop_core.vendor.langextract.core.schema", "Constraint"),
      "ConstraintType": ("loop_core.vendor.langextract.core.schema", "ConstraintType"),
      "EXTRACTIONS_KEY": ("loop_core.vendor.langextract.core.data", "EXTRACTIONS_KEY"),
      "ATTRIBUTE_SUFFIX": ("loop_core.vendor.langextract.core.data", "ATTRIBUTE_SUFFIX"),
      "FormatModeSchema": ("loop_core.vendor.langextract.core.schema", "FormatModeSchema"),
  }

  if name in core_items:
    mod, attr = core_items[name]
    warnings.warn(
        f"`langextract.schema.{name}` has moved to `{mod}.{attr}`. Please"
        " update your imports.",
        FutureWarning,
        stacklevel=2,
    )
    module = __import__(mod, fromlist=[attr])
    return getattr(module, attr)
  elif name == "GeminiSchema":
    from loop_core.vendor.langextract.providers.schemas.gemini import GeminiSchema
    return GeminiSchema

  raise AttributeError(f"module 'langextract.schema' has no attribute '{name}'")
