"""Pytest configuration ensuring the project root is on sys.path.

Allows importing `App.mcp_client_wrapper` even when pytest is invoked from
inside the `tests` directory (so CWD isn't the project root).
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
