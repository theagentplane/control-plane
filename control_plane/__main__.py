"""Run the control plane as a module: ``python -m control_plane <cmd>``.

Equivalent to the ``control-plane`` console script, but reachable without the
interpreter's ``Scripts``/``bin`` directory on PATH.
"""

from __future__ import annotations

from control_plane.cli import main

if __name__ == "__main__":
    main()
