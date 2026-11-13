"""Backward-compatibility shim — the package was renamed to `skullpt` in v1.2.

`import designshell` returns the `skullpt` package itself, and
`python3 -m designshell ...` runs the SKULLPT CLI, so the Forge bridge
and any old scripts keep working unchanged. New code should import
`skullpt` directly.
"""
import sys

import skullpt

sys.modules[__name__] = skullpt
