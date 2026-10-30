# ruff: noqa: E501
"""Finish saved Typst frames with an overview sheet, playback, and a viewer."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args()
    job = args.job
    deadline = time.monotonic() + 1800
    while not (job / "visualization-complete.json").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Rendering did not complete; inspect render.log")
        time.sleep(5)
    output = job / "visualization"
    images = sorted(output.glob("stage-*.png"))
    data = json.loads((output / "stages.json").read_text())
    if len(images) != len(data["frames"]):
        raise RuntimeError("The frame sequence is incomplete")
    selected = [0, 1, 4, 7, 10, 15, 16, 17]
    source = (
        "#set page(width: 360mm, height: 200mm, margin: 8mm, fill: white)\n"
        '#set text(font: "DejaVu Sans", size: 12pt)\n'
        "= Eastern Seaboard pruning\n"
        "#grid(columns: (1fr, 1fr, 1fr, 1fr), gutter: 2mm,\n"
        + "".join(f'image("{images[i].name}", width: 100%),\n' for i in selected)
        + ")\n"
    )
    (output / "overview.typ").write_text(source)
    for extension in ["pdf", "png"]:
        subprocess.run(
            [
                "typst",
                "compile",
                str(output / "overview.typ"),
                str(output / f"overview.{extension}"),
            ],
            check=True,
        )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "warning",
            "-framerate",
            "0.5",
            "-pattern_type",
            "glob",
            "-i",
            str(output / "stage-*.png"),
            "-vf",
            "tpad=stop_mode=clone:stop_duration=6,pad=ceil(iw/2)*2:ceil(ih/2)*2",
            "-r",
            "24",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output / "pruning-playback.mp4"),
        ],
        check=True,
    )
    frames = json.dumps(
        [dict(src=p.name, title=f["title"]) for p, f in zip(images, data["frames"], strict=True)]
    )
    html = """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Eastern Seaboard pruning</title>
<style>body{font-family:Arial,sans-serif;max-width:1000px;margin:20px auto;padding:0 12px;color:#111;background:white}img{width:100%;height:auto}button,input{min-height:40px}nav{display:flex;gap:12px;align-items:center;flex-wrap:wrap}input{flex:1;min-width:160px}a{color:#165b97}</style>
<h1>Eastern Seaboard pruning</h1>
<nav><button id="previous">Previous</button><input id="stage" type="range" min="0" value="0" aria-label="Pruning stage"><button id="next">Next</button><output id="label" aria-live="polite"></output></nav>
<img id="map" alt="Pruning map">
<p>Blue cells remain. Orange cells fail at the current scale. Green marks consistent terminal centers. Unresolved candidates remain elsewhere on the coast.</p>
<p><a href="pruning-sequence.pdf">Every stage as PDF</a> · <a href="pruning-playback.mp4">Playback</a></p>
<script>
const frames=FRAMES;
const stage=document.getElementById('stage'),map=document.getElementById('map'),label=document.getElementById('label');
stage.max=frames.length-1;
function show(){const i=Number(stage.value);map.src=frames[i].src;map.alt=frames[i].title;label.textContent=(i+1)+' / '+frames.length+' · '+frames[i].title;document.getElementById('previous').disabled=i===0;document.getElementById('next').disabled=i===frames.length-1;}
stage.addEventListener('input',show);
document.getElementById('previous').onclick=()=>{stage.value=Number(stage.value)-1;show()};
document.getElementById('next').onclick=()=>{stage.value=Number(stage.value)+1;show()};
show();
</script></html>
""".replace("FRAMES", frames)
    (output / "index.html").write_text(html)
    (job / "complete.json").write_text(
        json.dumps({"complete": True, "frames": len(images), "output": str(output)}, indent=2)
        + "\n"
    )
    print((job / "complete.json").read_text(), flush=True)


if __name__ == "__main__":
    main()
