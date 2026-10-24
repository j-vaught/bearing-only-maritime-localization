# Changelog

## Unreleased

This update adds a coast-wide water mask and candidate-specific range and land visibility pruning. Complete-report scoring now accounts for angularly merged catalog targets and missing visible detections. A reproducible B1–B5 full-catalog experiment records accuracy, latency, and data hashes.

## 0.1.0 — 2026-10-23

This initial public baseline adds a configurable 2D maritime scene generator, globally referenced noisy bearings, unknown AIS association with unmatched detections, a position-grid search with optional least-squares refinement, and CPU/CuPy residual-kernel profiling.

It also adds a reproducible ten-trial synthetic benchmark, exported failure-case data, three Typst-authored explanatory figures, a documented historical AIS evaluation plan, and software citation metadata. The benchmark does not use historical AIS and does not validate coast-wide performance.
