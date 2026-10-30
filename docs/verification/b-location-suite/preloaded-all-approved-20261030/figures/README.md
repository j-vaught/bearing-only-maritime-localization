# Preloaded GPU result figures.

J.C. Vaught.

These figures use the completed, approved 20-site results in the parent results.csv. Every site uses the corrected catalog and preloaded GPU maps. All points use the same blue square marker. No timeout values or catalog-cohort styling are included.

The time-versus-error and time-versus-aid-count figures retain the earlier full-range and detail layouts. Full-range panels include all 20 sites. The time-versus-error detail includes the 15 sites within 4 m and 8 seconds. The time-versus-aid-count detail includes the 16 sites within 8 seconds. Position error versus aid count includes all 20 sites.

Search times exclude shared input loading, GPU preload, simulation, and final selection. Error is the post-freeze WGS84 distance in meters. B1, B3, B10, and B23 remain outside the approved site set.

The figures are authored in relationships.typ and plot-common.typ using CeTZ. Data and the source CSV hash are in data.json. Each plotted row and both detail-panel counts were checked against the CSV. All three PNG exports were inspected for labels, bounds, and clipping. The combined PDF contains three pages.

Compile the complete PDF with `typst compile relationships.typ relationships.pdf`. Compile an individual image with `typst compile --input plot=time-error relationships.typ time-error.png --ppi 150`, substituting time-aids or error-aids for the other plots. Individual PDF exports use the same plot input and a PDF output filename.

Time versus charting aids includes an unweighted ordinary least-squares fit of natural-log search time against aid count using all 20 sites. The model is ln(t / 1 s) = 3.5438462813 - 0.0522255544 N, with R-squared 0.4858136458 in log time. It appears as a straight dashed line on the semilog panel and as the corresponding exponential curve on the linear detail panel. The detail is not refitted to its subset. Predictions are drawn only over the observed aid-count range of 18 through 97. The fit is descriptive across sites. No repeated-run timing uncertainty or causal interpretation is estimated.

Regenerate coefficients and plotted fit coordinates with `python fit_time_aids.py`. The numerical record is time-aids-fit.json. The source data hash, site membership, coefficients, log-time R-squared, sampled curve coordinates, and an independent centered-sum slope check are retained.
