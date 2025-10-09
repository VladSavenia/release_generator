import re
from typing import Dict

_TAG_RE = re.compile(
    r"^v(\d+)\.(\d+)\.(\d+)\.(\d+)-Rev(\d+)(-release)?$"
)

def _calc_minor(variant_num: int, minor_ver: int) -> int:
    # ((variant_num - 1) << 4) | minor_ver
    return ((variant_num - 1) << 4) | minor_ver

class ReleaseFormatter:
    """
    Единая точка генерации релизных строк (тег, имя контейнера) и их валидации.
    Принимает исходные словари json/defs, чтобы не привязываться к внутренностям ReleaseInfo.
    """

    @staticmethod
    def make_tag_name_from_dict(json_data: Dict, defs_data: Dict) -> str:
        proj_id      = int(defs_data["proj_id"])
        major_ver    = int(defs_data["major_ver"])
        minor_ver    = int(defs_data["minor_ver"])
        hard_num     = int(json_data["hard_num"])
        variant_num  = int(json_data["variant_num"])

         # формируем revision_ver
        is_service_firmware=json_data.get("is_service_firmware", False)
        if is_service_firmware:
            revision_ver = 255
        else:
            revision_ver = int(defs_data["revision_ver"])

        minor = _calc_minor(variant_num, minor_ver)
        tag = f"v{proj_id}.{major_ver}.{minor}.{hard_num}-Rev{revision_ver}"

        return tag

    @staticmethod
    def make_container_name_from_dict(json_data: Dict, defs_data: Dict) -> str:
        proj_id     = int(defs_data["proj_id"])
        major_ver   = int(defs_data["major_ver"])
        minor_ver   = int(defs_data["minor_ver"])
        hard_num    = int(json_data["hard_num"])
        variant_num = int(json_data["variant_num"])

        minor = _calc_minor(variant_num, minor_ver)
        return f"{proj_id}.{major_ver:03d}.{minor:03d}.{hard_num:03d}.btl.bin"

    @staticmethod
    def validate_tag_string(tag: str) -> bool:
        return bool(_TAG_RE.match(tag))
