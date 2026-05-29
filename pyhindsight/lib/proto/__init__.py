# Package marker for generated protobuf modules.
#
# The generated code imports each top-level chromium-mirrored package
# (`components.*`, `content.*`, ...) as if it were a top-level package.
# When importing via `pyhindsight.lib.proto`, alias each one so those
# imports resolve without requiring real top-level packages.
import sys as _sys

from . import components as _components
_sys.modules.setdefault("components", _components)
from . import content as _content
_sys.modules.setdefault("content", _content)
