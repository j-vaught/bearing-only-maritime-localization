"""Refresh saved-data figure and media provenance after editing the draft."""

import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def fingerprint(path):
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def main():
    path = ROOT / "figure-manifest.json"
    manifest = json.loads(path.read_text())
    slides = json.loads((ROOT / "slides.json").read_text())
    manifest["main_slide_count"] = len(slides["main"])
    manifest["backup_slide_count"] = len(slides["backup"])
    manifest["data_exports"] = {
        str(p.relative_to(ROOT)): fingerprint(p)
        for p in sorted((ROOT / "data").iterdir())
        if p.is_file()
    }
    figures = []
    for slide in slides["main"] + slides["backup"]:
        number = slide["number"]
        key = f"{number:02}" if isinstance(number, int) else number
        figures.append(
            {
                "slide": number,
                "title": slide["title"],
                "planned_asset": slide["asset"],
                "evidence_status": slide["evidence_status"],
                "drawing_source": "pruning/witness.typ"
                if key == "25"
                else "pruning/pruning-movie.typ"
                if key == "27"
                else "figures.typ; map.typ; technical.typ",
                "files": {
                    str(p.relative_to(ROOT)): fingerprint(p)
                    for p in [
                        ROOT / "figures" / f"{key}.{ext}" for ext in ["pdf", "png"]
                    ]
                },
            }
        )
    manifest["figures"] = figures
    manifest["media"] = {}
    for movie in sorted(ROOT.rglob("*.mp4")):
        probe = json.loads(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,nb_frames,r_frame_rate,duration",
                    "-of",
                    "json",
                    str(movie),
                ]
            )
        )["streams"][0]
        manifest["media"][str(movie.relative_to(ROOT))] = {
            **fingerprint(movie),
            **probe,
        }
    manifest["deck"] = {"file": "slides.pdf", **fingerprint(ROOT / "slides.pdf")}
    manifest["source_documents"] = {
        name: fingerprint(ROOT / name)["sha256"]
        for name in [
            "slides.typ",
            "slides.json",
            "notes.md",
            "figures.typ",
            "map.typ",
            "technical.typ",
        ]
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
