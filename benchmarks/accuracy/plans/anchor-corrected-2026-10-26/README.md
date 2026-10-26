# Anchor-corrected accuracy plan

This immutable plan corrects the geographic grouping calculation to use the reference-anchor coordinates from the 2024 port source. The previous controller used the port-point coordinates for the 26 km overlap graph, which incorrectly joined B7 and B8: their port points are 25.319 km apart while their reference anchors are 26.432 km apart. The unchanged threshold therefore keeps B8 in validation. The corrected graph has 12 development ports, 4 validation ports, and 8 held-out ports. No overlap component crosses the original split assignments.

The split manifest uses schema `accuracy-split-manifest-v2`. It is bound into `config.json` by SHA-256. The original plan files at the parent accuracy directory remain unchanged. Physical seeds continue to include the effective split, so B8 validation trials must be regenerated and prior B8 development inputs remain archived under their original identity.
