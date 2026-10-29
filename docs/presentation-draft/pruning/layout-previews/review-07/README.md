# Complete progressive-zoom still sequence.

J.C. Vaught.

All 17 frames are reconstructed from the saved pruning trace. No search, scoring replay, or video generation was performed. The initial grid, 15 pruning generations from 10 km through 0.6103515625 m, and the saved final scoring overlay are included.

The Charleston view retains the original extent through the 625 m generation. Each following generation doubles magnification, reaching 1024 times the original magnification at 0.6103515625 m. The small black cross with a white halo identifies the true simulated observer position in both panels at every stage. This position is used only for presentation.

The displayed retained and rejected counts cover the full coast for the current generation. At the tightest crop, 371 of the 397 retained cell centers are inside the Charleston panel and 26 are outside it. The complete geometry remains in the saved trace. Final center classifications are distinct from whole-cell pruning decisions.

Open `all-frames.pdf` to review every frame. The `frames` folder contains individual PNG and PDF exports. `frame-manifest.json` records exact extents, magnifications, counts, and trace hash. All graphics are authored with Typst, CeTZ, and Lilaq.

Regenerate from the presentation-draft root with the existing saved data and render helpers using `uv run --with numpy --with shapely --with pyproj python pruning/layout-previews/review-07/render_all_frames.py`. Typst and Poppler are required.
