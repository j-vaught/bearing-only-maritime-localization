"""Coalesce chart aids within 20 cm, retaining complete source components."""

from __future__ import annotations

import argparse
import gzip
from pathlib import Path

from maritime.catalog_identity import canonicalize_chart_aids
from maritime.schemas.inputs_v2 import CatalogV2
from maritime.serialization import dumps_contract, loads_contract


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Choose a separate output file so source rows remain preserved.")
    if args.output.exists():
        parser.error("Output already exists; choose a new destination.")
    data = args.input.read_bytes()
    if args.input.suffix == ".gz":
        data = gzip.decompress(data)
    original = loads_contract(data.decode("utf-8"), CatalogV2)
    catalog = canonicalize_chart_aids(original)
    encoded = (dumps_contract(catalog) + "\n").encode("utf-8")
    if args.output.suffix == ".gz":
        encoded = gzip.compress(encoded, mtime=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as stream:
        stream.write(encoded)
    print(f"Wrote {len(catalog.records)} catalog records from {len(original.records)} source rows.")


if __name__ == "__main__":
    main()
