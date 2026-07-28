"""Load and validate the wallet's JSON taxonomy seeds."""

from functools import lru_cache
import json
from pathlib import Path
import re


SEED_DIR = Path(__file__).parent
VALID_CATEGORY_TYPES = frozenset({"expense", "income", "transaction"})
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


class SeedValidationError(ValueError):
    pass


def _load(filename):
    with (SEED_DIR / filename).open(encoding="utf-8") as handle:
        return json.load(handle)


def _require_mapping(value, message):
    if not isinstance(value, dict):
        raise SeedValidationError(message)


def _require_nonempty_string(value, message):
    if not isinstance(value, str) or not value.strip():
        raise SeedValidationError(message)


def _require_hex_color(value, message):
    if not isinstance(value, str) or not HEX_COLOR.fullmatch(value):
        raise SeedValidationError(message)


def _validate_category_seed(payload):
    _require_mapping(payload, "category seed must be an object")
    _require_nonempty_string(payload.get("version"), "category seed version is required")
    roots = payload.get("roots")
    if not isinstance(roots, list) or not roots:
        raise SeedValidationError("category seed roots are required")
    ids = set()
    root_names = set()
    for root in roots:
        _require_mapping(root, "category root must be an object")
        _require_nonempty_string(root.get("id"), "category root id is required")
        _require_nonempty_string(root.get("name"), "category root name is required")
        if root["id"] in ids or root["name"] in root_names:
            raise SeedValidationError("duplicate category root")
        if root.get("type") not in VALID_CATEGORY_TYPES:
            raise SeedValidationError("invalid category root type")
        children = root.get("children")
        if not isinstance(children, list) or not children:
            raise SeedValidationError("category root must have children")
        ids.add(root["id"])
        root_names.add(root["name"])
        child_names = set()
        for child in children:
            _require_mapping(child, "category child must be an object")
            _require_nonempty_string(child.get("id"), "category child id is required")
            _require_nonempty_string(child.get("name"), "category child name is required")
            if child["id"] in ids or child["name"] in child_names:
                raise SeedValidationError("duplicate category child")
            ids.add(child["id"])
            child_names.add(child["name"])
    if not isinstance(payload.get("legacy_mappings", []), list):
        raise SeedValidationError("legacy_mappings must be a list")
    for mapping in payload.get("legacy_mappings", []):
        _require_mapping(mapping, "legacy mapping must be an object")
        source = mapping.get("from")
        if not isinstance(source, list) or len(source) != 2:
            raise SeedValidationError("legacy mapping source must be a primary/secondary pair")
        for name in source:
            _require_nonempty_string(name, "legacy mapping source name is required")
        _require_nonempty_string(mapping.get("to"), "legacy mapping target is required")
        if mapping["to"] not in ids:
            raise SeedValidationError("legacy mapping target does not exist")
        if not isinstance(mapping.get("review", False), bool):
            raise SeedValidationError("legacy mapping review must be boolean")


def _validate_tag_seed(payload):
    _require_mapping(payload, "tag seed must be an object")
    _require_nonempty_string(payload.get("version"), "tag seed version is required")
    tags = payload.get("tags")
    if not isinstance(tags, list) or not tags:
        raise SeedValidationError("tag seed tags are required")
    ids = set()
    names = set()
    for tag in tags:
        _require_mapping(tag, "tag must be an object")
        for field in ("id", "name", "group"):
            _require_nonempty_string(tag.get(field), "tag " + field + " is required")
        if tag["id"] in ids or tag["name"] in names:
            raise SeedValidationError("duplicate tag")
        ids.add(tag["id"])
        names.add(tag["name"])


def _validate_category_color_seed(payload, category_seed):
    _require_mapping(payload, "category color seed must be an object")
    _require_nonempty_string(payload.get("version"), "category color seed version is required")
    _require_nonempty_string(payload.get("theme"), "category color seed theme is required")
    _require_hex_color(payload.get("fallback_color"), "category color fallback_color must be a hex color")
    groups = {
        "expense_colors": "expense",
        "income_colors": "income",
        "transfer_colors": "transaction",
    }
    roots_by_type = {
        category_type: [root for root in category_seed["roots"] if root["type"] == category_type]
        for category_type in VALID_CATEGORY_TYPES
    }
    for key, category_type in groups.items():
        colors = payload.get(key)
        _require_mapping(colors, key + " must be an object")
        roots = roots_by_type[category_type]
        expected_names = {root["name"] for root in roots}
        if set(colors) != expected_names:
            raise SeedValidationError(key + " must match category seed roots")
        for root in roots:
            color_entry = colors[root["name"]]
            if category_type == "expense":
                _require_mapping(color_entry, "expense category color must be an object")
                _require_nonempty_string(color_entry.get("icon"), "expense category icon is required")
                _require_hex_color(color_entry.get("color"), "expense category color must be a hex color")
                children = color_entry.get("children")
                _require_mapping(children, "expense category children must be an object")
                expected_children = {child["name"] for child in root["children"]}
                if set(children) != expected_children:
                    raise SeedValidationError("expense category children must match category seed")
                for child_color in children.values():
                    _require_hex_color(child_color, "expense child color must be a hex color")
            else:
                _require_hex_color(color_entry, key + " values must be hex colors")


def _category_color_mapping(category_seed, color_seed):
    mapping = {"_fallback": {"color": color_seed["fallback_color"], "root_color": color_seed["fallback_color"], "icon": ""}}
    sections = {
        "expense": color_seed["expense_colors"],
        "income": color_seed["income_colors"],
        "transaction": color_seed["transfer_colors"],
    }
    for root in category_seed["roots"]:
        entry = sections[root["type"]][root["name"]]
        if root["type"] == "expense":
            root_color = entry["color"]
            icon = entry["icon"]
            children = entry["children"]
        else:
            root_color = entry
            icon = ""
            children = {}
        mapping[root["id"]] = {"color": root_color, "root_color": root_color, "icon": icon}
        for child in root["children"]:
            mapping[child["id"]] = {
                "color": children.get(child["name"], root_color),
                "root_color": root_color,
                "icon": icon,
            }
    return mapping


@lru_cache
def load_category_seed():
    payload = _load("category_seed.json")
    _validate_category_seed(payload)
    return payload


@lru_cache
def load_category_color_seed():
    category_seed = load_category_seed()
    payload = _load("category_colors.json")
    _validate_category_color_seed(payload, category_seed)
    return _category_color_mapping(category_seed, payload)


@lru_cache
def load_tag_seed():
    payload = _load("tag_seed.json")
    _validate_tag_seed(payload)
    return payload
