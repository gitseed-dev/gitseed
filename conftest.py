"""Repository-wide pytest setup.

Settings fail closed on default/weak secrets. Unit tests run against throwaway
in-memory fakes, so opt into the documented escape hatch *before* any test module
imports ``gitseed_core.config`` (the developer's local .env may hold defaults).
"""

import os

os.environ.setdefault("GITSEED_ALLOW_INSECURE_DEFAULTS", "true")
