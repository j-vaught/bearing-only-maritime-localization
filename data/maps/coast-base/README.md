# NOAA ENC coast base

This candidate contains full-domain NOAA Electronic Navigational Chart (ENC) General and Coastal coverage and land layers for the eastern-seaboard benchmark extent, from longitude -82 to -66 degrees and latitude 24.5 to 45.5 degrees. The source layers are separate from the existing fine port maps. Coastal geometry has precedence over General geometry in the MapV2 candidate.

The acquisition manifest records each service and layer URL, metadata hashes, object-ID field, exact requested and returned ID sets, query response hashes, retrieval time, original source attributes and geometries, and archive hashes. The four source archives contain 12 General coverage features, 1,530 General land features, 88 Coastal coverage features, and 5,689 Coastal land features. Each layer has complete OID retrieval and zero invalid polygon IDs. Coverage records report CATCOV as “coverage available.” The source SORDAT values remain attached to their original features.

The MapV2 candidate is coast-base.map-v2.json.gz. Its coverage status is partial because its known NOAA coverage occupies 2,579,766.7 km², or 76.23% of the 3,384,168.6 km² benchmark bounding box. The remaining 23.77% is explicitly unknown. The reported extent of known coverage spans the full bounding-box envelope; it does not mean every point inside that envelope is covered. Land geometry remains separate from chart coverage, and no land or water is inferred outside the NOAA coverage polygons.

During initial retrieval, NOAA ArcGIS returned the JSON error code 500 with the message “Could not find service. Service may be stopped or it may not be configured.” for the full-domain General Land object-ID query on the three configured hosts. Subsequent bounded retries succeeded with an exact full-domain ID set. The final manifest records the successful query and archive hashes, with no unresolved tiles.

The MapV2 file is a coarse base candidate for composition with the existing fine port maps. Its source retrieval completeness does not imply complete domain coverage or a validated final coast mosaic.
