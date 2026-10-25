# Observer test locations

b1-b5.csv contains the original Chesapeake Bay pilot positions used by the first search benchmark.

east-coast-ports-b1-b10.csv is a separate, expanded set of ten synthetic observer positions near major East Coast port approaches. The B1–B10 identifiers are local to this file and do not replace the original Chesapeake B1–B5 sites. Coordinates were selected from 500 m cells in the project's East Coast land/water mask. The last column records an approximate port-area anchor used during placement and the straight-line separation from that anchor.

Every selected point is water according to the 500 m Natural Earth mask. This mask is suitable for coarse experiments, not for determining whether a point is navigable. Its coastline representation omits some narrow river channels, so Savannah and Jacksonville use water cells near the river entrances rather than points beside inland terminals. Charted navigation channels and higher-resolution shoreline data are needed for precise terminal-adjacent placements.

The selected sites are spread along the coast from Boston to Miami. They are suitable for comparing search methods across different chart-aid and vessel densities, but should be reviewed on the companion map before running the localization benchmark.

Port names and regions follow the U.S. Army Corps of Engineers North Atlantic Division navigation overview and the leading-port summary.

- https://www.nad.usace.army.mil/Missions/Civil-Works/Navigation/
- https://www.iwr.usace.army.mil/Missions/Navigation/Navigation-Data-and-Information/US-Port-Statistics/

## Atlantic Coast major-port set

east-coast-major-ports-top150-2024.csv contains one synthetic observer position for each Atlantic-coast entry in the U.S. Army Corps of Engineers 2024 Leading U.S. Ports table ranked in its top 150 by total tonnage. The set contains 24 port areas from Searsport, Maine, to PortMiami. Albany is excluded because its port is far up the Hudson River and the project mask does not resolve that inland channel. The B1–B24 identifiers are local to this file and are independent of the B1–B10 IDs in the earlier test set.

Each point is the nearest water-cell center to an approximate port-area anchor in the project's 500 m Natural Earth mask. The CSV includes the anchor, separation, and placement note so the choice is auditable. Boston through Philadelphia and the other harbor sites use their nearby harbor water. The Delaware and Cape Fear River ports, Savannah, Brunswick, and Jacksonville use nearby approaches when the coarse mask does not represent all inland channels.

The companion full-size map is docs/figures/east-coast-major-ports.pdf. The port list comes from the USACE 2024 fact card and its leading-port table.

- https://publibrary.sec.usace.army.mil/api/download?filename=FactCard2024.pdf&id=3dfa3db1-63b9-4510-9bab-ca4b1ba2ca41&preview=true
- https://www.nad.usace.army.mil/Missions/Civil-Works/Navigation/
