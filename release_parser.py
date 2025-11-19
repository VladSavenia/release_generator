import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict
from .release_formatter import ReleaseFormatter

MIN_MINOR_VERSION = 1
MAX_MINOR_VERSION = 15

@dataclass
class TargetInfo:
    target_name: str
    tag_name: str
    container_name: str
    hard_num: int
    variant_num: int

@dataclass
class ReleaseInfo:
    git_project_id: int
    branch_name: str
    is_service_firmware: bool
    upgrade_to_release: bool
    features: List[str]
    bug_fixes: List[str]
    targets: List[TargetInfo]

class ReleaseParser:
    """
    Parser: responsible only for loading input data and extracting versions from defs.h.
    Tag/container formatting is delegated to ReleaseFormatter.
    """

    def __init__(self, json_file_path: str, defs_file_path: str):
        self._json_file_path = json_file_path
        self._defs_file_path = defs_file_path

    def parse(self) -> ReleaseInfo:
        with open(self._json_file_path, encoding="utf-8") as f:
            json_data = json.load(f)

        # Required JSON fields
        required = ['cmake_project_name', 'git_project_id', 'branch_name', 'targets']
        for field in required:
            if field not in json_data:
                raise ValueError(f"Missing required field '{field}' in {self._json_file_path}")

        if not json_data['targets']:
            raise ValueError("'targets' array cannot be empty")

        # defs.h
        defs_data = self.parse_defs(self._defs_file_path)

        # Create TargetInfo objects for each hard_num/variant_num combination
        targets = []
        for target in json_data['targets']:
            if 'hard_num' not in target or 'variant_num' not in target:
                raise ValueError("Each target must have 'hard_num' and 'variant_num'")

            # Create a copy of json_data for each target, updating hard_num and variant_num
            target_json = json_data.copy()
            target_json.update(target)

            target_name = f"{json_data['cmake_project_name']}_hard{target['hard_num']}_var{target['variant_num']}"
            tag_name = ReleaseFormatter.make_tag_name_from_dict(target_json, defs_data)
            container_name = ReleaseFormatter.make_container_name_from_dict(target_json, defs_data)

            targets.append(TargetInfo(
                target_name=target_name,
                tag_name=tag_name,
                container_name=container_name,
                hard_num=int(target['hard_num']),
                variant_num=int(target['variant_num'])
            ))

        return ReleaseInfo(
            branch_name=json_data.get("branch_name"),
            git_project_id=int(json_data.get("git_project_id")),
            is_service_firmware=bool(json_data.get("is_service_firmware", False)),
            upgrade_to_release=bool(json_data.get("upgrade_to_release", False)),
            features=list(json_data.get("features", [])),
            bug_fixes=list(json_data.get("bug_fixes", [])),
            targets=targets
        )

    def parse_defs(self, defs_path: str) -> Dict[str, int]:
        """Parses defs.h and returns a dictionary of versions"""
        text = Path(defs_path).read_text(encoding="utf-8")

        def get_define(name: str) -> int:
            # Captures variants with #define NAME (123) as well
            pattern = rf"#define\s+{name}\s+\(?(\d+)\)?"
            m = re.search(pattern, text)
            if not m:
                raise ValueError(f"Define '{name}' not found in text")
            return int(m.group(1))

        versions = {
            "proj_id": get_define("PRODUCT_ID"),
            "major_ver": get_define("PRODUCT_VERSION"),
            "minor_ver": get_define("PRODUCT_VARIANT_MINOR_VER"),
            "revision_ver": get_define("PRODUCT_REVISION"),
        }

        # Validate minor version range
        if not MIN_MINOR_VERSION <= versions["minor_ver"] <= MAX_MINOR_VERSION:
            raise ValueError(f"Minor version must be between {MIN_MINOR_VERSION} and {MAX_MINOR_VERSION} (inclusive), got {versions['minor_ver']}")

        return versions
