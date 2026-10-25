# Observer test locations

b1-b5.csv contains the original Chesapeake Bay pilot positions used by the first search benchmark.

east-coast-ports-b1-b10.csv is a separate, expanded set of ten synthetic observer positions near major East Coast port approaches. The B1–B10 identifiers are local to this file and do not replace the original Chesapeake B1–B5 sites. Coordinates were selected from 500 m cells in the project's East Coast land/water mask. The last column records an approximate port-area anchor used during placement and the straight-line separation from that anchor.

Every selected point is water according to the 500 m Natural Earth mask. This mask is suitable for coarse experiments, not for determining whether a point is navigable. Its coastline representation omits some narrow river channels, so Savannah and Jacksonville use water cells near the river entrances rather than points beside inland terminals. Charted navigation channels and higher-resolution shoreline data are needed for precise terminal-adjacent placements.

The selected sites are spread along the coast from Boston to Miami. They are suitable for comparing search methods across different chart-aid and vessel densities, but should be reviewed on the companion map before running the localization benchmark.

Port names and regions follow the U.S. Army Corps of Engineers North Atlantic Division navigation overview and the leading-port summary.

- https://www.nad.usace.army.mil/Missions/Civil-Works/Navigation/
- https://www.iwr.usace.army.mil/Missions/Navigation/Navigation-Data-and-Information/US-Port-Statistics/
