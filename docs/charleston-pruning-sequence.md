# Charleston pruning sequence

The map sequence starts with all 39,435 search cells covering the Eastern Seaboard. Each subsequent view shows a finer dyadic cell scale. Blue rectangles survive the region pruning predicates. Orange rectangles fail at that scale. The displayed bounds include every surviving region, so the map only zooms when their combined extent shrinks.

The sequence replays the existing GPU pruning predicates on devices 0–2 with the original saved inputs and caches. It records every evaluated rectangle and its decision. Terminal scoring is disabled during the replay. The recorded traversal matches the original run exactly, including all 1,337,411 evaluated cells, 976,768 rejected cells, and 36,149 terminal rectangles.

The final two views use the original saved terminal center scores. There are 148 consistent centers near Charleston and 15,441 unresolved centers elsewhere in the declared search domain. The Charleston view shows the consistent cluster separately. Unresolved candidates remain in the overview. An inconsistent terminal center does not prove that its entire rectangle is impossible.

The PDF provides every stage as a separate page. The image sequence and playback use the same Typst-authored frames. Small rectangles are enlarged to one display pixel and coincident display pixels are merged for visibility. The NPZ trace retains the original coordinates and individual decisions without display aggregation. The nominal terminal cell width is 0.6103515625 m because subdivision halves the initial 10 km width until it falls below the 1 m stopping threshold.

The recorded rejection codes distinguish a rectangle entirely within known land, a positive marker observation without any possible matching object, and too few distinct possible markers. Marker pruning includes range, bearing bounds, and the existing conservative land-blocking proof. Missing detections are not penalized. The display groups the actual combined pruning decisions by spatial scale rather than inventing separate temporal passes for each predicate.

All map frames are authored with Typst and CeTZ. Display shoreline simplification affects rendering only. It does not change the saved map, index, pruning predicates, or original position estimate.

Written by J.C. Vaught.
