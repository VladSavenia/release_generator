import re
from typing import Dict

_TAG_RE = re.compile(
    r"^v(?P<proj>\d+)\.(?P<major>\d+)\.(?P<minor>\d+)\.(?P<hard>\d+)-"
    r"Rev(?P<revision>\d+)(?P<suffix>-release)?$"
)

_MAX_COMPONENT_VALUE = 255
_MAX_VARIANT = 16
_MAX_MINOR = 15


def _calc_minor(variant_num: int, minor_ver: int) -> int:
    """Calculate the packed minor value used in tags and filenames."""

    if not 1 <= variant_num <= _MAX_VARIANT:
        raise ValueError("variant_num must be in range [1, 16]")
    if not 1 <= minor_ver <= _MAX_MINOR:
        raise ValueError("minor_ver must be in range [1, 15]")

    packed_variant = min(variant_num, _MAX_VARIANT - 1) - 1
    return (packed_variant << 4) | minor_ver

class ReleaseFormatter:
    """
    Единая точка генерации релизных строк (тег, имя контейнера) и их валидации.
    Принимает исходные словари json/defs, чтобы не привязываться к внутренностям ReleaseInfo.
    """

    @staticmethod
    def make_tag_name_from_dict(json_data: Dict, defs_data: Dict) -> str:
        proj_id = int(defs_data["proj_id"])
        major_ver = int(defs_data["major_ver"])
        minor_ver = int(defs_data["minor_ver"])
        hard_num = int(json_data["hard_num"])
        variant_num = int(json_data["variant_num"])

        is_service_firmware = json_data.get("is_service_firmware", False)
        if is_service_firmware:
            revision_ver = _MAX_COMPONENT_VALUE
        else:
            revision_ver = int(defs_data["revision_ver"])

        minor = _calc_minor(variant_num, minor_ver)
        return f"v{proj_id}.{major_ver}.{minor}.{hard_num}-Rev{revision_ver}"

    @staticmethod
    def make_container_name_from_dict(json_data: Dict, defs_data: Dict) -> str:
        proj_id = int(defs_data["proj_id"])
        major_ver = int(defs_data["major_ver"])
        minor_ver = int(defs_data["minor_ver"])
        hard_num = int(json_data["hard_num"])
        variant_num = int(json_data["variant_num"])

        minor = _calc_minor(variant_num, minor_ver)
        return f"{proj_id}.{major_ver:03d}.{minor:03d}.{hard_num:03d}.btl.bin"

    @staticmethod
    def validate_tag_string(tag: str) -> bool:
        match = _TAG_RE.match(tag)
        if not match:
            return False

        try:
            proj = int(match.group("proj"))
            major = int(match.group("major"))
            minor = int(match.group("minor"))
            hard = int(match.group("hard"))
            revision = int(match.group("revision"))
        except (ValueError, TypeError):
            return False

        return all(
            0 < value <= _MAX_COMPONENT_VALUE
            for value in (proj, major, minor, hard, revision)
        )
