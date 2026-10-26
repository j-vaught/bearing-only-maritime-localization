# Shared input data

Every data file is directly in this folder. All search examples use the same full East Coast object catalog and vector map.

| File | Contents |
|---|---|
| `objects.json.gz` | Current search catalog with 2,268 AIS records and 64,400 chart-aid components. |
| `map.json.gz` | Current land geometry and explicit known/unknown coverage. |
| `objects.manifest.json`, `map.manifest.json` | Compressed and uncompressed checksums, coordinate conventions, counts and source information. |
| `map-source-validation.json` | Source geometry validation record. |
| `observer_locations.csv` | 24 East Coast port-area observer locations for simulation and evaluation. |
| `east-coast-observers-b1-b10.csv` | Earlier ten-port observer locations. |
| `*2023-07-15T1200Z.csv` | Selected AIS snapshot source tables. |
| `*-enc-aids.csv` | Chart feature source tables before catalog normalization. |

Adjacent source-table manifests retain original extraction details. AIS row identifiers are snapshot keys; the original extraction omitted vessel names and MMSIs. Chart components can have unresolved physical identities. Source manifests can mention historical server paths or filenames; those are provenance, not files needed at runtime.

Observer coordinates are simulator/evaluator truth, never search inputs. Identifiers belong to their individual observer tables. These points were originally selected using a coarse mask; the examples must check them against the current vector map before simulation.

The catalog and map use EPSG:4326. The declared search domain uses EPSG:32618. Approximately 23.7 percent of the map region has unknown coverage, which is not inferred as water.

Data sources are [NOAA historical AIS](https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2023/AIS_2023_07_15.zip), [NOAA ENC Direct](https://nauticalcharts.noaa.gov/learn/encdirect/), and [USACE port statistics](https://www.iwr.usace.army.mil/Missions/Navigation/Navigation-Data-and-Information/US-Port-Statistics/).
