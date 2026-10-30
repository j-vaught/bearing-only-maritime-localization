"""Copy saved presentation assets into a manual slide assembly package."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil

from pypdf import PdfReader, PdfWriter

SOURCE = Path(__file__).resolve().parent
TARGET = Path.home() / "Downloads/maritime-ppt-assembly"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def copy_checked(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if digest(source) != digest(target):
        raise RuntimeError(f"Copy mismatch {target}")


def main() -> None:
    if TARGET.exists():
        raise SystemExit(f"Refusing to overwrite {TARGET}")
    TARGET.mkdir()
    content = json.loads((SOURCE / "slides.json").read_text())
    slides = content["main"] + content["backup"]
    reader = PdfReader(SOURCE / "slides.pdf")
    if len(slides) != 41 or len(reader.pages) != len(slides):
        raise RuntimeError("Slide count mismatch")
    rows = []
    records = []
    for page_index, slide in enumerate(slides):
        number = slide["number"]
        asset = f"{number:02}" if isinstance(number, int) else number
        identity = (
            f"{page_index + 1:02}"
            if isinstance(number, int)
            else f"{page_index + 1:02}-{number}"
        )
        name = identity + "-" + slug(slide["title"])
        folder = TARGET / "slides" / name
        folder.mkdir(parents=True)
        page_path = folder / (name + ".pdf")
        writer = PdfWriter()
        writer.add_page(reader.pages[page_index])
        writer.add_metadata(
            {
                "/Author": "J.C. Vaught",
                "/Title": slide["title"],
                "/Subject": f"Technical review slide {number}.",
            }
        )
        with page_path.open("wb") as stream:
            writer.write(stream)
        copied = PdfReader(page_path)
        original = reader.pages[page_index]
        if len(copied.pages) != 1:
            raise RuntimeError(f"Page count mismatch {page_path}")
        copied_stream = copied.pages[0].get_contents()
        original_stream = original.get_contents()
        if copied_stream is None or original_stream is None:
            raise RuntimeError(f"Missing page content {page_path}")
        if copied_stream.get_data() != original_stream.get_data():
            raise RuntimeError(f"Page content mismatch {page_path}")
        if copied.pages[0].mediabox != original.mediabox:
            raise RuntimeError(f"Page size mismatch {page_path}")
        for ext in ("pdf", "png"):
            copy_checked(
                SOURCE / "figures" / f"{asset}.{ext}",
                folder / "figures" / f"{name}-figure.{ext}",
            )
        notes = (
            "\n\n".join(
                [
                    "# " + str(number) + ". " + slide["title"],
                    "Written by J.C. Vaught.",
                    slide["notes"],
                    "Transition. " + slide["transition"],
                    "Sources. " + "; ".join(slide["source_paths"]) + ".",
                ]
            )
            + "\n"
        )
        (folder / "speaker-notes.md").write_text(notes)
        relative = page_path.relative_to(TARGET).as_posix()
        rows.append(
            f"| {page_index + 1} / 41 | {number} | {slide['title']} | [Slide PDF]({relative}) |"
        )
        records.append(
            {
                "page": page_index + 1,
                "slide_id": number,
                "title": slide["title"],
                "pdf": relative,
                "content_stream_sha256": hashlib.sha256(
                    original_stream.get_data()
                ).hexdigest(),
                "figure_source": f"figures/{asset}.pdf",
            }
        )
    reference = TARGET / "reference"
    copy_checked(SOURCE / "slides.pdf", reference / "complete-draft.pdf")
    copy_checked(SOURCE / "notes.md", reference / "meeting-notes.md")
    copy_checked(SOURCE / "slides.json", reference / "slide-content.json")
    videos = {
        "scene-to-bearings.mp4": SOURCE / "videos/scene-to-bearings.mp4",
        "recorded-result-overview.mp4": SOURCE / "videos/recorded-result-overview.mp4",
        "pruning-playback.mp4": SOURCE / "pruning/movie/pruning-playback.mp4",
    }
    for name, path in videos.items():
        copy_checked(path, TARGET / "shared-media" / name)
    copy_checked(
        SOURCE / "pruning/movie/storyboard.pdf",
        TARGET / "shared-media/pruning-storyboard.pdf",
    )
    source_tree = TARGET / "editable-source"
    source_tree.mkdir()
    for path in SOURCE.iterdir():
        if path.is_file() and path.suffix in {".typ", ".py", ".json"}:
            copy_checked(path, source_tree / path.name)
    for path in (SOURCE / "data").rglob("*"):
        if path.is_file():
            copy_checked(path, source_tree / path.relative_to(SOURCE))
    for path in (SOURCE / "pruning").rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in {
            ".typ",
            ".py",
            ".json",
            ".jsonl",
            ".npz",
            ".svg",
            ".command",
        }:
            continue
        if "build" in path.relative_to(SOURCE).parts or path.name == "concat.txt":
            continue
        copy_checked(path, source_tree / path.relative_to(SOURCE))
    (source_tree / "figures").mkdir(exist_ok=True)
    (source_tree / "videos").mkdir(exist_ok=True)
    (source_tree / "README.md").write_text("""# Editable figure sources

Written by J.C. Vaught.

These are the saved Typst sources and data behind the draft. The rendered figure PDFs and PNGs are in the per-slide folders, while the published videos are in shared-media. The figures and videos output directories here start empty to avoid storing duplicate outputs.

Run `uv run --no-project python render.py` from this folder to render all figures. Then run `typst compile slides.typ slides.pdf` to rebuild the complete draft. The pruning video can be rebuilt with `uv run --no-project --with numpy --with shapely python pruning/build_pruning_movie.py --output pruning/movie`.

All authored figures use Typst with CeTZ or Lilaq. Rendering from the supplied recorded data requires no server or GPU search.
""")
    (TARGET / "slide-index.md").write_text(
        "# Slide index\n\n| PDF page | Slide ID | Title | File |\n|---|---|---|---|\n"
        + "\n".join(rows)
        + "\n"
    )
    (TARGET / "README.md").write_text("""# Manual presentation assembly

Written by J.C. Vaught.

The slides folder contains 41 folders in presentation order. Each folder includes one single-page slide PDF, a figures folder containing the figure-only vector PDF and PNG used on that slide, and the full speaker notes. The body figure excludes the slide title and page/section footer, so it can be placed independently in your presentation. Multiview figures retain their original combined composition. Their editable Typst sources allow individual panels to be revised.

Open slide-index.md for the slide order and links. Pages 1–33 are the main presentation. Pages 34–41 are backups A1–A8. The reference folder contains the complete draft and meeting notes. The shared-media folder contains the three published video clips and the 17-stage pruning storyboard. These are the published reference media, not a new video preview.

For manual assembly, use the figure PNG when a bitmap is convenient, or the vector PDF when your presentation software accepts it. Add the slide title and copy speaker-notes.md into your notes area. The single-page slide PDF provides the original composition as a reference or a complete page image.

The editable-source folder contains the figure sources and recorded data. No slide, plot, video or scientific result was changed while making this package. Every split page was checked against the corresponding original content stream and page size, and every figure copy was verified by SHA-256.
""")
    manifest = {
        "schema": "ManualPresentationAssemblyV1",
        "author": "J.C. Vaught",
        "source_data_revision": "41fb6f9",
        "main_slides": 33,
        "backup_slides": 8,
        "single_page_slide_pdfs": 41,
        "separate_figure_pdfs": 41,
        "separate_figure_pngs": 41,
        "published_videos": 3,
        "page_identity_verified": True,
        "figure_copies_sha256_verified": True,
        "source_pdf_sha256": digest(SOURCE / "slides.pdf"),
        "slides": records,
    }
    (TARGET / "assembly-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    forbidden = re.compile(r"openai|codex|chatgpt|gpt", re.I)
    bad = []
    for path in TARGET.rglob("*"):
        if path.is_file() and path.suffix in {".md", ".json", ".py", ".typ", ".txt"}:
            if forbidden.search(path.read_text()):
                bad.append(str(path.relative_to(TARGET)))
    if bad:
        raise RuntimeError(f"Restricted terms found {bad}")
    total = sum(path.stat().st_size for path in TARGET.rglob("*") if path.is_file())
    print(
        json.dumps(
            {
                "folder": str(TARGET),
                "bytes": total,
                "slide_pdfs": 41,
                "figure_pdfs": 41,
                "figure_pngs": 41,
                "videos": 3,
                "verified": True,
            }
        )
    )


if __name__ == "__main__":
    main()
