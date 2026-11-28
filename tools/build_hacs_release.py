"""Build the HACS release ZIP from the committed integration."""

import argparse
import json
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "automower_ble_plus"
COMPONENT = f"custom_components/{DOMAIN}"


def release_notes(tag: str) -> str:
    """Return the committed changelog section for this tag."""
    changelog = subprocess.check_output(
        ["git", "show", "HEAD:CHANGELOG.md"], cwd=ROOT, text=True
    )
    for section in re.split(r"(?m)^## ", changelog)[1:]:
        heading, _, body = section.partition("\n")
        if heading.strip() == tag and body.strip():
            return body.strip() + "\n"
    raise ValueError(f"CHANGELOG.md must include release notes under '## {tag}'")


def build_release(
    output_dir: Path, tag: str | None, notes_file: Path | None = None
) -> Path:
    """Write the HACS archive and optionally its published release notes."""
    manifest = json.loads(
        subprocess.check_output(
            ["git", "show", f"HEAD:{COMPONENT}/manifest.json"], cwd=ROOT
        )
    )
    metadata = json.loads(
        subprocess.check_output(["git", "show", "HEAD:hacs.json"], cwd=ROOT)
    )
    if manifest["domain"] != DOMAIN:
        raise ValueError("The integration domain must match the component directory")
    if not metadata["zip_release"] or metadata["filename"] != f"{DOMAIN}.zip":
        raise ValueError("hacs.json must declare the integration release ZIP")
    expected_tag = f"v{manifest['version']}"
    if tag is not None and tag != expected_tag:
        raise ValueError(f"The integration manifest requires tag {expected_tag}")
    notes = release_notes(expected_tag)

    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / metadata["filename"]
    license_text = subprocess.check_output(["git", "show", "HEAD:LICENSE"], cwd=ROOT)
    commit_time = subprocess.check_output(
        ["git", "show", "-s", "--format=%cI", "HEAD"], cwd=ROOT, text=True
    ).strip()
    with TemporaryDirectory(dir=output_dir) as temp:
        archive = Path(temp).resolve() / destination.name
        subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                f"--mtime={commit_time}",
                f"--output={archive}",
                f"HEAD:{COMPONENT}",
            ],
            check=True,
            cwd=ROOT,
        )
        with ZipFile(archive, "a") as package:
            package.writestr(
                ZipInfo("LICENSE"), license_text, compress_type=ZIP_DEFLATED
            )
            package.writestr(
                ZipInfo("release_notes.md"),
                f"**{expected_tag}**\n\n{notes}",
                compress_type=ZIP_DEFLATED,
            )
        archive.replace(destination)
    if notes_file is not None:
        notes_file.write_text(notes, encoding="utf-8")
    return destination.resolve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--tag")
    parser.add_argument("--notes-file", type=Path)
    options = parser.parse_args()
    print(build_release(options.output_dir, options.tag, options.notes_file))
