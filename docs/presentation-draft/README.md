# Bearing-only maritime localization

Technical review draft by J.C. Vaught. Prepared 29 October 2026.

Open [the slide draft](slides.pdf) to review the 33 main slides and eight backups. [slides.typ](slides.typ) controls the composition, while [slides.json](slides.json) and [notes.md](notes.md) contain the slide content and full narration. Videos appear as labeled poster placeholders in the PDF. PowerPoint production follows the review of this draft.

The figures use archived National Oceanic and Atmospheric Administration electronic navigational chart geometry, chart features and Automatic Identification System positions. The Charleston observations are the exact saved simulated frame. The result is the saved Eastern Seaboard search with 64 aid-bearing constraints, a 2.060304 m position error and an 8.461936 s search API wall time. No new localization search or simulator execution was used to prepare this draft.

## Figures and media

`figures/` contains each authored figure as a vector PDF and a 1920-pixel PNG. `figures.typ`, `map.typ` and `technical.typ` author every map, plot and diagram using Typst and CeTZ. `data/` retains the drawing inputs and source hashes. Mathematical constructions are identified separately from recorded observations and performance measurements.

[Scene to bearings](videos/scene-to-bearings.mp4) progressively reveals the saved 70-report frame. [Recorded result overview](videos/recorded-result-overview.mp4) shows the domain, selected Charleston estimate and final terminal centers. These are presentation reveals of saved records. Their playback duration is independent of search runtime.

The detailed synchronized pruning movie remains the slide 27 placeholder. Its intermediate rejected and retained rectangles were not preserved in the current run. A current spatial decision recording is required before that movie and actual rejection-graph examples can be added. The older algorithm's movie has not been substituted. The proposed combined chart-aid and observed-AIS comparison on slide 31 has no performance bars because that experiment is not yet implemented and measured.

The one-, three- and five-kilometer scene panels filter the saved ten-kilometer physical-object and line-of-sight records. They do not run new simulations. Their visible/in-range counts are 6/6, 45/54 and 70/102. The ten-kilometer panel is 80/200. The noise histogram recovers 69 physical-report errors from the saved representative links and excludes the additional vessel report.

## Display conventions

Coordinates use EPSG 32618 projected meters. Map arrows labeled $N_G$ show grid north. The heading-conversion figure separately draws true north using a WGS84 geodesic. Its 30-degree heading is a declared coordinate-conversion calculation on actual geographic positions. The saved search observations already contain global bearings.

Local shoreline drawings use three-meter display simplification of the exact projected map used by the recorded run. The coast overview additionally suppresses components smaller than its display pixel and uses coarser display simplification. The complete source map, catalog and caches remain unchanged. Coast overview geometry must never be substituted for simulator visibility or search input geometry. White indicates unknown map coverage, pale blue indicates known coverage with land overlaid in gray, blue squares indicate chart objects and orange triangles indicate AIS-source records. Hollow blue squares retain unknown chart classification.

The meter plots classify tested terminal centers. They do not classify every point of the surrounding cells. Their spread is not a confidence interval. The current raw feasibility graph preserves possible explanations without full polygon line-of-sight edge rejection; the simulator and nominal final predictor apply full polygon visibility. These distinctions are discussed in the notes.

## Editing and rendering

Edit slide titles and narration in `slides.json`, and retain the matching prose in `notes.md`. Edit the figure compositions in `figures.typ`, shared map drawing in `map.typ`, and mathematical diagrams in `technical.typ`. Each slide uses the PNG with its two-digit main slide number or backup identifier. The main draft uses the revised outline, with slide 26 titled “Deterministic marker scoring” to match its mathematical figure. Unresolved positions are explained on slide 30 and the backups.

Run the following commands in this directory. Typst, the CeTZ package, Poppler and FFmpeg are required. The imported data are already supplied, so rendering requires no server access, cache construction or new experiments.

```sh
uv run --no-project python prepare_render_data.py
uv run --no-project python render.py
typst compile slides.typ slides.pdf
uv run --no-project python render_videos.py
```

To render one edited figure, use `uv run --no-project python render.py 17`, then compile the draft again. `extract_data.py` documents extraction from the authoritative saved repository and run files on the server. Re-extraction is unnecessary when changing layouts. The saved source data and every final figure are listed in `figure-manifest.json`.

## Timing scope

The main timing slide reports search API wall time. Geographic index construction is performed once for a fixed dataset and is excluded. The standalone process read and validated the existing saved inputs in 7.388215 s before the API call. The 8.461936 s API call includes its device setup, uploads, coordination and output. The diagnostic supervisor took 23.449189 s, including preservation checks and separate selection/evaluation steps. Those three quantities measure different scopes. This run does not establish persistent-server steady-state GPU latency.
