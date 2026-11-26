"""Read Automower map features and edit active site geometry by ID."""

from itertools import pairwise
from xml.dom import minidom
from xml.etree.ElementTree import Element

from .mapfiles import SVG_NAMESPACE, MapKind, parse_map_svg

_AREA_TYPES = {"green": "work_area", "red": "stay_out_zone", "blue": "transport_area"}
_PATH_TYPES = {
    "blue": "transport_path",
    "cyan": "poi_path",
    "yellow": "charging_station",
}
_READ_ONLY = {"charging_station", "unknown"}
MIN_COORDINATE = -(1 << 31)
MAX_COORDINATE = (1 << 31) - 1
type Point = tuple[int, int]


def _feature_type(shape: Element, kind: MapKind) -> str:
    if shape.tag.removeprefix(SVG_NAMESPACE) == "polyline":
        return _PATH_TYPES.get(shape.get("stroke", "").lower(), "unknown")
    fill = shape.get("fill", "").lower()
    if fill == "red":
        if kind is MapKind.GENERATED:
            return "island"
        if shape.get("rewildingzone", "").lower() == "true":
            return "rewilding_zone"
    return _AREA_TYPES.get(fill, "unknown")


def _points(shape: Element) -> list[dict[str, int]]:
    return [
        {"x_mm": int(x), "y_mm": int(y)}
        for x, y in (point.split(",") for point in shape.attrib["points"].split())
    ]


def describe_map_features(svg: str, kind: MapKind, writable: bool) -> list[dict]:
    """Describe the SVG features without exposing raw XML in the response."""
    _, root = parse_map_svg(
        svg, require_unique_ids=kind is MapKind.SITE, allow_empty=True
    )
    features = []
    for shape in root:
        feature_type = _feature_type(shape, kind)
        feature = {
            "id": shape.get("id"),
            "type": feature_type,
            "name": shape.get("title", ""),
            "enabled": shape.get("display", "").lower() != "none",
            "editable": writable and feature_type not in _READ_ONLY,
            "points": _points(shape),
        }
        if (width := shape.get("stroke-width")) is not None:
            try:
                feature["width_mm"] = int(width)
            except ValueError:
                pass
        features.append(feature)
    return features


def _turn(a: Point, b: Point, c: Point) -> int:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _validate_geometry(vertices: list[Point], polygon: bool) -> None:
    if polygon and vertices and vertices[0] == vertices[-1]:
        vertices = vertices[:-1]
    if len(vertices) < (3 if polygon else 2) or len(vertices) != len(set(vertices)):
        raise ValueError("The map feature needs distinct points")
    segments = list(pairwise([*vertices, vertices[0]] if polygon else vertices))
    # Compare segments with overlapping bounding boxes using exact integer turns.
    active = []
    boxes = sorted(
        (min(a[0], b[0]), max(a[0], b[0]), min(a[1], b[1]), max(a[1], b[1]), a, b)
        for a, b in segments
    )
    for left, right, bottom, top, a, b in boxes:
        active = [segment for segment in active if segment[0] >= left]
        for _, low, high, c, d in active:
            if top < low or high < bottom:
                continue
            if a in (c, d) or b in (c, d):
                if _turn(a, b, c) or _turn(a, b, d):
                    continue
                # Collinear neighbours meet only at their shared endpoint.
                shared, other = (c, d) if c in (a, b) else (d, c)
                axis = int(a[0] == b[0])
                outer = b if shared == a else a
                if (outer[axis] - shared[axis]) * (other[axis] - shared[axis]) < 0:
                    continue
            elif (
                _turn(a, b, c) * _turn(a, b, d) > 0
                or _turn(c, d, a) * _turn(c, d, b) > 0
            ):
                continue
            raise ValueError("The map feature crosses or overlaps itself")
        active.append((right, bottom, top, a, b))


def update_map_geometry(svg: str, feature_id: str, points: list[dict[str, int]]) -> str:
    """Update only the coordinates of an existing area or path."""
    raw, root = parse_map_svg(svg)
    shape = next((shape for shape in root if shape.get("id") == feature_id), None)
    if shape is None:
        raise ValueError("The active map does not contain the selected feature")
    if _feature_type(shape, MapKind.SITE) in _READ_ONLY:
        raise ValueError("The selected map feature does not support geometry editing")
    vertices = [(point["x_mm"], point["y_mm"]) for point in points]
    if any(
        type(value) is not int or not MIN_COORDINATE <= value <= MAX_COORDINATE
        for vertex in vertices
        for value in vertex
    ):
        raise ValueError("Coordinates outside the mower's signed millimetre range")
    _validate_geometry(vertices, shape.tag.removeprefix(SVG_NAMESPACE) == "polygon")
    if points == _points(shape):
        return svg
    # The mower's XML reader expects the map's original namespace and element names.
    with minidom.parseString(raw) as document:
        for node in document.documentElement.childNodes:
            if (
                node.nodeType == node.ELEMENT_NODE
                and node.getAttribute("id") == feature_id
            ):
                node.setAttribute("points", " ".join(f"{x},{y}" for x, y in vertices))
                break
        return document.documentElement.toxml()
