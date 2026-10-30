"""Compile authored Typst figures and slide draft. No experiment execution."""

import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def render_one(figure):
    target = ROOT / "figures" / figure
    compiled = subprocess.run(
        [
            "typst",
            "compile",
            "--input",
            f"figure={figure}",
            str(ROOT / "figures.typ"),
            str(target.with_suffix(".pdf")),
        ],
        text=True,
        capture_output=True,
    )
    if compiled.returncode:
        return figure, compiled.stderr
    raster = subprocess.run(
        [
            "pdftoppm",
            "-png",
            "-singlefile",
            "-scale-to-x",
            "1920",
            "-scale-to-y",
            "-1",
            str(target.with_suffix(".pdf")),
            str(target),
        ],
        text=True,
        capture_output=True,
    )
    return figure, raster.stderr if raster.returncode else None


def main():
    figures = sys.argv[1:] or [f"{i:02}" for i in range(1, 34)] + [
        f"A{i}" for i in range(1, 9)
    ]
    failed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for figure, error in executor.map(render_one, figures):
            if error:
                failed.append(figure)
                print(figure, error, flush=True)
            else:
                print(figure, "rendered", flush=True)
    if failed:
        raise SystemExit(f"Figure compilation failed {failed}")
    manifest = {
        "source": "figures.typ; map.typ; technical.typ",
        "rendering": "Typst/CeTZ to vector PDF; PDF rasterized for slide assembly",
        "figures": sorted(p.stem for p in (ROOT / "figures").glob("*.pdf")),
        "no_model_executions": True,
    }
    (ROOT / "render-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
