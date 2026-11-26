"""Read Automower map features and edit active site geometry by ID."""

from itertools import pairwise
from xml.dom import minidom
from xml.etree.ElementTree import Element

from .mapfiles import SVG_NAMESPACE, MapKind, parse_map_svg
from .models import DEFAULT_MAP_PATH_WIDTH_MINIMUM

_AREA_TYPES = {"green": "work_area", "red": "stay_out_zone", "blue": "transport_area"}
_PATH_TYPES = {
    "blue": "transport_path",
    "cyan": "poi_path",
    "yellow": "charging_station",
}
_READ_ONLY = {"charging_station", "unknown"}
_PATH_WIDTH_TYPES = {"transport_path", "poi_path"}
_MAIN_AREA_ID = "main_area_0"
_NEW_AREAS = {"work_area": "green", "stay_out_zone": "red"}
_NEW_PATHS = {"transport_path": "blue", "poi_path": "cyan"}
CREATABLE_MAP_TYPES = (*_NEW_AREAS, *_NEW_PATHS)
MAX_PATH_WIDTH = 5000
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


def has_parking_destination(svg: str) -> bool:
    """Check that the active map has one enabled point-of-interest path."""
    _, root = parse_map_svg(svg, allow_empty=True)
    paths = [
        shape for shape in root if _feature_type(shape, MapKind.SITE) == "poi_path"
    ]
    return len(paths) == 1 and paths[0].get("display", "").lower() != "none"


def describe_map_features(
    svg: str,
    kind: MapKind,
    writable: bool,
    path_width_min: int = DEFAULT_MAP_PATH_WIDTH_MINIMUM,
) -> list[dict]:
    """Describe the SVG features without exposing raw XML in the response."""
    _, root = parse_map_svg(
        svg, require_unique_ids=kind is MapKind.SITE, allow_empty=True
    )
    features = []
    for shape in root:
        feature_type = _feature_type(shape, kind)
        editable = (
            writable
            and kind is MapKind.SITE
            and feature_type not in _READ_ONLY
            and shape.get("id") != _MAIN_AREA_ID
        )
        feature = {
            "id": shape.get("id"),
            "type": feature_type,
            "name": shape.get("title", ""),
            "enabled": shape.get("display", "").lower() != "none",
            "editable": editable,
            "points": _points(shape),
        }
        if (width := shape.get("stroke-width")) is not None:
            try:
                feature["width_mm"] = int(width)
            except ValueError:
                pass
        if editable and feature_type != "work_area":
            feature["editable_settings"] = ["name", "enabled"]
            if feature_type in _PATH_WIDTH_TYPES:
                feature["editable_settings"].append("width_mm")
                feature["width_limits_mm"] = {
                    "min": path_width_min,
                    "max": MAX_PATH_WIDTH,
                }
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


def _find_editable_feature(svg: str, feature_id: str) -> tuple[bytes, Element]:
    raw, root = parse_map_svg(svg, allow_empty=True)
    shape = next((shape for shape in root if shape.get("id") == feature_id), None)
    if shape is None:
        raise ValueError("The active map does not contain the selected feature")
    if feature_id == _MAIN_AREA_ID or _feature_type(shape, MapKind.SITE) in _READ_ONLY:
        raise ValueError("The selected map feature does not support editing")
    return raw, shape


def _set_feature_attributes(
    raw: bytes, feature_id: str, attributes: dict[str, str | None]
) -> str:
    # Preserve the original SVG namespace and other attributes.
    with minidom.parseString(raw) as document:
        for node in document.documentElement.childNodes:
            if (
                node.nodeType == node.ELEMENT_NODE
                and node.getAttribute("id") == feature_id
            ):
                for key, value in attributes.items():
                    if value is not None:
                        node.setAttribute(key, value)
                    elif node.hasAttribute(key):
                        node.removeAttribute(key)
                break
        return document.documentElement.toxml()


def update_map_geometry(svg: str, feature_id: str, points: list[dict[str, int]]) -> str:
    """Update only the coordinates of an existing area or path."""
    raw, shape = _find_editable_feature(svg, feature_id)
    encoded = _encode_points(
        points, polygon=shape.tag.removeprefix(SVG_NAMESPACE) == "polygon"
    )
    if points == _points(shape):
        return svg
    return _set_feature_attributes(raw, feature_id, {"points": encoded})


def _encode_points(points: list[dict[str, int]], *, polygon: bool) -> str:
    vertices = [(point["x_mm"], point["y_mm"]) for point in points]
    if any(
        type(value) is not int or not MIN_COORDINATE <= value <= MAX_COORDINATE
        for vertex in vertices
        for value in vertex
    ):
        raise ValueError("Coordinates outside the mower's signed millimetre range")
    _validate_geometry(vertices, polygon)
    return " ".join(f"{x},{y}" for x, y in vertices)


def _validate_name(name: str) -> None:
    if not name.strip() or any(ord(character) < 32 for character in name):
        raise ValueError("Enter a nonempty map feature name without control characters")


def _validate_width(width_mm: int, minimum: int) -> None:
    if not minimum <= width_mm <= MAX_PATH_WIDTH:
        raise ValueError(f"Path width must be {minimum} to {MAX_PATH_WIDTH} mm")


def add_map_feature(
    svg: str,
    feature_id: str,
    feature_type: str,
    name: str,
    points: list[dict[str, int]],
    width_mm: int | None,
    path_width_min: int,
) -> str:
    """Append an app-supported polygon or path to the active site map."""
    if feature_type not in CREATABLE_MAP_TYPES:
        raise ValueError("Unsupported new map feature type")
    raw, root = parse_map_svg(svg, allow_empty=True)
    if any(shape.get("id") == feature_id for shape in root):
        raise ValueError("The map already contains this feature ID")
    if feature_type == "poi_path" and any(
        _feature_type(shape, MapKind.SITE) == "poi_path" for shape in root
    ):
        raise ValueError("The active map already has a point of interest path")
    polygon = feature_type in _NEW_AREAS
    _validate_name(name)
    attributes = {
        "id": feature_id,
        "title": name,
        "points": _encode_points(points, polygon=polygon),
    }
    if polygon:
        if width_mm is not None:
            raise ValueError("Only map paths have a width")
        attributes.update(
            fill=_NEW_AREAS[feature_type], stroke=_NEW_AREAS[feature_type]
        )
    else:
        if width_mm is None:
            raise ValueError("Enter the width of the new path in millimetres")
        _validate_width(width_mm, path_width_min)
        attributes.update(
            {"stroke": _NEW_PATHS[feature_type], "stroke-width": str(width_mm)}
        )
    tag = "polygon" if polygon else "polyline"
    with minidom.parseString(raw) as document:
        base = document.documentElement
        qualified_tag = f"{base.prefix}:{tag}" if base.prefix else tag
        node = document.createElementNS(base.namespaceURI, qualified_tag)
        for key, value in attributes.items():
            node.setAttribute(key, value)
        base.appendChild(node)
        return base.toxml()


def remove_map_feature(svg: str, feature_id: str) -> tuple[str, str]:
    """Remove one feature and return its type for mission consistency checks."""
    raw, shape = _find_editable_feature(svg, feature_id)
    with minidom.parseString(raw) as document:
        base = document.documentElement
        for node in base.childNodes:
            if (
                node.nodeType == node.ELEMENT_NODE
                and node.getAttribute("id") == feature_id
            ):
                base.removeChild(node)
                break
        return base.toxml(), _feature_type(shape, MapKind.SITE)


def update_map_feature_settings(
    svg: str,
    feature_id: str,
    name: str | None,
    enabled: bool | None,
    width_mm: int | None,
    path_width_min: int,
) -> str:
    """Change a map object's metadata without altering geometry or mission IDs."""
    if name is None and enabled is None and width_mm is None:
        raise ValueError("Select at least one map feature setting")
    raw, shape = _find_editable_feature(svg, feature_id)
    feature_type = _feature_type(shape, MapKind.SITE)
    if feature_type == "work_area":
        raise ValueError(
            "Use the work area action to change the associated mission settings"
        )
    attributes: dict[str, str | None] = {}
    if name is not None:
        _validate_name(name)
        if name != shape.get("title"):
            attributes["title"] = name
    if enabled is not None and enabled != (shape.get("display", "").lower() != "none"):
        attributes["display"] = None if enabled else "none"
    if width_mm is not None:
        if feature_type not in _PATH_WIDTH_TYPES:
            raise ValueError("Only map paths have an editable width")
        _validate_width(width_mm, path_width_min)
        if str(width_mm) != shape.get("stroke-width"):
            attributes["stroke-width"] = str(width_mm)
    return _set_feature_attributes(raw, feature_id, attributes) if attributes else svg
