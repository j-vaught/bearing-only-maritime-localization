#!/bin/zsh
set -eu
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
VIDEO_PACKAGE_ROOT="${0:A:h:h}"
cd "$VIDEO_PACKAGE_ROOT"
uv run --no-project --with numpy --with shapely python \
  pruning/build_pruning_movie.py --output pruning/movie "$@"
