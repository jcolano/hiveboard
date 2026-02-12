# Team 2: HiveLoop SDK — agent instrumentation client
import sys as _sys

from sdk import hiveloop  # noqa: F401

# Register sdk.hiveloop as 'hiveloop' in sys.modules so `import hiveloop` works
_sys.modules.setdefault("hiveloop", hiveloop)
_sys.modules.setdefault("hiveloop._transport", hiveloop._transport)
_sys.modules.setdefault("hiveloop._agent", hiveloop._agent)
