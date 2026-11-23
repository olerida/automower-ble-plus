"""Resolve installation limits using the mower's main node version."""

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

_LEGACY = re.compile(r"App-G3_devicetype-([0-9]+)\.([0-9]+)(?![0-9])")
_FAMILY = re.compile(r"^([0-9]+\.[0-9]+)_Main-App-")


@dataclass(frozen=True, slots=True)
class InstallationLimits:
    """The settings applicable to a single firmware profile."""

    reverse_min: int
    reverse_max: int
    angle_min: int
    angle_max: int


@cache
def _catalog() -> dict:
    return json.loads(
        Path(__file__).with_name("installation_profiles.json").read_text()
    )


def get_installation_limits(
    model_id: tuple[int, int], node_version: str | None
) -> InstallationLimits | None:
    """Look up the exact build, the app's family, or a matching G3 model."""
    if not node_version:
        return None
    catalog = _catalog()
    if node_version in catalog["release"]:
        profile = catalog["release"][node_version]
    elif match := _LEGACY.search(node_version):
        device, variant = map(int, match.groups())
        if model_id != (device, variant):
            return None
        profile = catalog["legacy"].get(f"{device}.{variant}")
    elif match := _FAMILY.match(node_version):
        profile = catalog["family"].get(match[1])
    else:
        return None
    return (
        InstallationLimits(*catalog["profiles"][profile])
        if profile is not None
        else None
    )
