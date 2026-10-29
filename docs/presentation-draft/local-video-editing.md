# Local pruning video editing

The editing package contains the complete recorded pruning trace, parent and root identities, batch records, frozen terminal results, coast and Charleston map exports, real adjacency witnesses, Typst sources, rendered frames, storyboard and current video. Rebuilding these visuals does not require the server, its large search cache, a GPU or another search.

The package has this layout.

| Path | Purpose |
|---|---|
| `pruning/movie/pruning-playback.mp4` | Current 38-second video. |
| `pruning/movie/storyboard.pdf` | All 17 recorded stages. |
| `pruning/pruning-movie.typ` | Edit titles, labels, colors, symbols and panel layout. |
| `pruning/build_pruning_movie.py` | Edit stage selection, zoom rules and frame timing. |
| `pruning/pruning-trace.npz` | All 64,875 recorded region decisions with exact hierarchy. |
| `pruning/frozen-terminal-results.npz` | The original 397 terminal cells and their saved scores. |
| `data/` | Coast and Charleston geometry, object positions and saved result metadata. |
| `pruning/pruning-graphs/` | Recorded incidence snapshots used for the rejection example. |

To rebuild, double-click `pruning/rebuild-video.command` or run the following command from the package folder. The current Mac already has Typst, FFmpeg and uv. The command uses NumPy and Shapely through uv.

```sh
uv run --no-project --with numpy --with shapely python \
  pruning/build_pruning_movie.py --output pruning/movie
```

For a different playback length, add `--duration 45`. The current builder accepts 30–45 seconds. Use `--fps 30` to change the frame rate. Use `--output pruning/movie-edited` to preserve the current video while creating an edited version. The output folder must remain inside the package because Typst resolves its map and image paths against this package root.

To prepare stage data without compiling figures or encoding video, add `--data-only`. All authored figures remain in Typst with CeTZ. Python prepares data and geometry, while FFmpeg assembles the rendered frames.

The animation groups recorded events by generation. Its playback duration is separate from search runtime. Magnified coastal cells and merged display pixels aid visibility; exact rectangles remain in the trace. The source data have already passed the recorded count and terminal-geometry parity checks. The editing package includes those checks and hashes as provenance.
