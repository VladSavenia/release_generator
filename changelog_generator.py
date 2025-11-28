import os
import logging
import re
from datetime import datetime
from typing import List, Set

import gitlab
import gitlab.exceptions

from .release_parser import ReleaseInfo
from .gitlab_rep import GitlabRep

logger = logging.getLogger(__name__)

JIRA_TASK_PATTERN = re.compile(r'\[([\w\-]+)\]')
JIRA_BASE_URL = "https://smfactory.atlassian.net/browse"

class ChangelogGenerator:
    def __init__(self):
        pass

    def _extract_jira_tasks(self, items: List[str]) -> Set[str]:
        """Extract unique Jira task IDs from a list of strings."""
        tasks = set()
        for item in items:
            match = JIRA_TASK_PATTERN.search(item)
            if match:
                tasks.add(match.group(1))
        return tasks

    def _format_task_link(self, description: str) -> str:
        """Format a task with Jira hyperlink."""
        match = JIRA_TASK_PATTERN.search(description)
        if not match:
            # If task is not found, just return the original text
            return f"- {description}"
        task_id = match.group(1)
        return f"- [[{task_id}]({JIRA_BASE_URL}/{task_id})] {description.replace(f'[{task_id}]', '').strip()}"

    def generate_changelog_entries(self, info: ReleaseInfo, rep: GitlabRep) -> str:
        """
        Build changelog entries for the provided ReleaseInfo and repository.

        Returns a string fragment that can be inserted into an existing CHANGELOG.md
        (it already starts with a separating blank line).
        This method is public so callers can reuse the changelog-building logic
        without performing the push.
        """
        current_date = datetime.now().strftime("%d-%m-%Y")
        new_entries: list[str] = []
        new_entries.append("\n")  # Add blank line for separation

        for target in info.targets:
            release_tag = target.tag_name + "-release"
            new_entries.append(f"## Version `{release_tag}` - {current_date}\n\n")

            # Collect all Jira tasks
            feature_tasks = self._extract_jira_tasks(info.features)
            bugfix_tasks = self._extract_jira_tasks(info.bug_fixes)
            all_tasks = feature_tasks.union(bugfix_tasks)

            # Add Jira tasks section if there are any tasks
            if all_tasks:
                new_entries.append("Version related with tasks:")
                sorted_tasks = sorted(all_tasks)
                for i, task in enumerate(sorted_tasks):
                    if i == len(sorted_tasks) - 1:  # last element
                        new_entries.append(f"`{task}`")
                    else:
                        new_entries.append(f"`{task}`, ")
                new_entries.append("\n")

            # Add firmware files section
            new_entries.append("### Firmware files:\n\n")
            tag_url = rep.get_tag(release_tag)
            bin_path = f"build/{target.target_name}.bin"
            container_path = f"build/{target.container_name}"

            new_entries.append(f"- [{release_tag}]({tag_url}/{bin_path})\n")
            new_entries.append(f"- [container]({tag_url}/{container_path})\n\n")

            # Add Features section if exists
            if info.features:
                new_entries.append("### New Features:\n\n")
                for feature in info.features:
                    new_entries.append(self._format_task_link(feature) + "\n")
                new_entries.append("\n")

            # Add Bug Fixes section if exists
            if info.bug_fixes:
                new_entries.append("### Bug Fixes:\n\n")
                for fix in info.bug_fixes:
                    new_entries.append(self._format_task_link(fix) + "\n")
                new_entries.append("\n")

        return ''.join(new_entries)

    def update_changelog_and_push(self, info: ReleaseInfo, rep: GitlabRep, branch_name: str, commit_message: str) -> str:
        """
        Update CHANGELOG.md with release information using GitLab API.
        Creates a new branch from the default branch and pushes changes to it.

        Args:
            info: Release information object
            rep: GitLab repository object
            branch_name: Name of the branch to create/update
            commit_message: Commit message for the changelog update

        Returns:
            The full content of the updated CHANGELOG.md as a string.
        """

        # Try to get existing content from GitLab
        project = rep.get_project_obj()
        try:
            # Try to create branch if it doesn't exist
            try:
                project.branches.create({
                    'branch': branch_name,
                    'ref': project.default_branch
                })
                logger.info(f"Created new branch: {branch_name}")
            except gitlab.exceptions.GitlabCreateError:
                logger.info(f"Branch {branch_name} already exists")

            # Try to get existing content
            try:
                file_obj = project.files.get(file_path='CHANGELOG.md', ref=branch_name)
                content = file_obj.decode().decode('utf-8').splitlines(keepends=True)
            except gitlab.exceptions.GitlabGetError:
                content = ["# Changelog\n"]

            # Generate fragment with new entries and insert after title
            new_entries = self.generate_changelog_entries(info, rep)
            for i, line in enumerate(content):
                if line.startswith("# Changelog"):
                    content[i:i+1] = [line] + [new_entries]
                    break

            # Prepare content for GitLab API
            full_content = ''.join(content)

            # Update or create file using GitLab API
            file_data = {
                'branch': branch_name,
                'commit_message': commit_message,
                'content': full_content,
                'file_path': 'CHANGELOG.md'
            }

            try:
                # Try to update existing file
                file_obj = project.files.get(file_path='CHANGELOG.md', ref=branch_name)
                file_obj.content = full_content
                file_obj.save(branch=branch_name, commit_message=commit_message)
                logger.info("Updated CHANGELOG.md in branch %s", branch_name)
            except gitlab.exceptions.GitlabGetError:
                # File doesn't exist, create it
                project.files.create(file_data)
                logger.info("Created CHANGELOG.md in branch %s", branch_name)

            # Create Merge Request with CHANGELOG changes to the default repository branch
            rep.merge_request_to_default("Обновление CHANGELOG", branch_name)

            # Return the final changelog content for callers that want to use it
            return full_content

        except Exception as e:
            logger.error(f"Failed to update changelog using GitLab API: {e}")
            raise