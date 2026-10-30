"""Assemble presentation media from Typst-rendered saved-data views."""

import concurrent.futures
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FRAMES = ROOT / "build" / "video-frames"


def render_frame(index):
    subprocess.run(
        [
            "typst",
            "compile",
            "--input",
            "library=true",
            "--input",
            f"reveal={index / 23}",
            "--ppi",
            "152.4",
            str(ROOT / "video-scene.typ"),
            str(FRAMES / f"scene-{index:02}.png"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def main():
    FRAMES.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(render_frame, range(24)))
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-framerate",
            "2",
            "-i",
            str(FRAMES / "scene-%02d.png"),
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            "-t",
            "12",
            "-movflags",
            "+faststart",
            str(ROOT / "videos" / "scene-to-bearings.mp4"),
        ],
        check=True,
    )
    for stage in ("02", "29", "30"):
        subprocess.run(
            [
                "typst",
                "compile",
                "--input",
                f"stage={stage}",
                "--ppi",
                "152.4",
                str(ROOT / "video-result.typ"),
                str(FRAMES / f"result-{stage}.png"),
            ],
            check=True,
        )
    listing = FRAMES / "result-frames.txt"
    listing.write_text(
        "".join(
            f"file '{FRAMES / ('result-' + stage + '.png')}'\nduration 4\n"
            for stage in ("02", "29", "30")
        )
        + f"file '{FRAMES / 'result-30.png'}'\n"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c:v",
            "libx264",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            "-t",
            "12",
            "-movflags",
            "+faststart",
            str(ROOT / "videos" / "recorded-result-overview.mp4"),
        ],
        check=True,
    )
    manifest = {
        "scene-to-bearings.mp4": {
            "kind": "progressive reveal of one saved 70-report frame",
            "duration_s": 12,
            "source": "data/simulation.json; video-scene.typ",
        },
        "recorded-result-overview.mp4": {
            "kind": "three saved geographic/result views; no intermediate pruning masks",
            "duration_s": 12,
            "source": "figures/02.png; figures/29.png; figures/30.png; video-result.typ",
        },
        "current_pruning_trace": "Awaiting a separately approved spatial decision recording. No old trace substituted.",
        "no_model_execution": True,
    }
    (ROOT / "videos" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
