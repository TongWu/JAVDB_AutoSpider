"""Thin ASGI/bootstrap alias for the canonical API runtime module."""

from __future__ import annotations

import importlib
import sys

sys.modules[__name__] = importlib.import_module("apps.api.services.runtime")
