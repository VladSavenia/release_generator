import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

class GeneratorTag:
    def __init__(self, rep, name: str, features: List[str], bug_fixes: List[str], ref: str):
        self.__rep = rep
        self.__name = name
        self.__features = features
        self.__bug_fixes = bug_fixes
        self.__ref = ref

    def generate(self) -> str:
        """
        Creates a tag if it doesn't exist yet. Returns the tag view URL.
        """
        description_parts: List[str] = []

        if self.__features:
            description_parts.append("Features:")
            description_parts.extend(self.__features)

        if self.__bug_fixes:
            description_parts.append("Bug Fixes:")
            description_parts.extend(self.__bug_fixes)

        description = "\n".join(description_parts).strip()

        existing = self.__rep.get_tag(name=self.__name)
        if existing is not None:
            logger.info("Tag '%s' already exists: %s", self.__name, existing)
            return existing

        created = self.__rep.make_tag(name=self.__name, desc=description, ref=self.__ref)
        return created