#!/usr/bin/env sh
# Regenerate Python protobuf modules from the .proto files under
# pyhindsight/lib/components/. Output lands in pyhindsight/lib/proto/components/
# (mirroring the source tree layout).
#
# Usage: ./regen-protos.sh
# Requires: pip install grpcio-tools  (pinned in requirements-dev.txt)
set -e

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
SRC_ROOT="$REPO_ROOT/pyhindsight/lib"
PROTO_ROOT="$SRC_ROOT/components"
OUT_ROOT="$SRC_ROOT/proto"

if [ ! -d "$PROTO_ROOT" ]; then
  echo "No .proto sources found at $PROTO_ROOT" >&2
  exit 1
fi

# Wipe previously-generated files so deletions in the source tree propagate.
if [ -d "$OUT_ROOT/components" ]; then
  rm -rf "$OUT_ROOT/components"
fi
mkdir -p "$OUT_ROOT"

# Preserve the package shim that aliases `components` for cross-file imports.
cat > "$OUT_ROOT/__init__.py" <<'EOF'
# Package marker for generated protobuf modules.
#
# The generated code imports `components.*` as a top-level package. When
# importing via `pyhindsight.lib.proto`, alias `components` so those imports
# resolve without requiring a separate top-level `components` package.
import sys as _sys

from . import components as _components

_sys.modules.setdefault("components", _components)
EOF

PROTOS=$(find "$PROTO_ROOT" -name "*.proto")
if [ -z "$PROTOS" ]; then
  echo "No .proto files found under $PROTO_ROOT" >&2
  exit 1
fi

python -m grpc_tools.protoc --python_out="$OUT_ROOT" -I "$SRC_ROOT" $PROTOS

echo "Generated $(echo "$PROTOS" | wc -l | tr -d ' ') _pb2.py files into $OUT_ROOT/components/"
