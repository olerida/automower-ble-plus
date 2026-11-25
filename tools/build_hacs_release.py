"""Build the HACS release ZIP from the committed integration."""

import argparse
import json
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "automower_ble_plus"
COMPONENT = f"custom_components/{DOMAIN}"


def build_release(output_dir: Path, tag: str | None) -> Path:
    """Write the release archive at the path declared by hacs.json."""
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
        archive.replace(destination)
    return destination.resolve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    parser.add_argument("--tag")
    options = parser.parse_args()
    print(build_release(options.output_dir, options.tag))
