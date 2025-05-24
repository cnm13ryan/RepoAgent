"""Utilities for temporary file management during documentation generation.

This module encapsulates the side effects required to snapshot unstaged
Python files. `FakeFileManager.make_temp_versions` renames the working
copy to ``*_latest_version.py`` and writes the diff output back to the
original path so other components operate on a clean tree. Once the
documentation generation completes, call
`FakeFileManager.delete_temp_versions` to restore or remove these
temporary files.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

import git
from colorama import Fore, Style

from repo_agent.log import logger
from repo_agent.settings import SettingsManager

latest_version_substring = "_latest_version.py"


class FakeFileManager:
    """Manage temporary versions of Python files."""

    def __init__(self, repo_path: Path | None = None) -> None:
        setting = SettingsManager.get_setting()
        self.repo_path = Path(repo_path or setting.project.target_repo)
        self.repo = git.Repo(self.repo_path)

    # ------------------------------------------------------------------
    def make_temp_versions(self) -> tuple[dict[str, str], list[str]]:
        """Create temporary files representing unstaged changes."""
        self.delete_temp_versions()

        unstaged_changes = self.repo.index.diff(None)
        untracked_files = self.repo.untracked_files

        skipped_files: list[str] = []
        for file_name in untracked_files:
            if file_name.endswith(".py"):
                print(
                    f"{Fore.LIGHTMAGENTA_EX}[SKIP untracked files]: {Style.RESET_ALL}{file_name}"
                )
                skipped_files.append(file_name)

        for diff_file in unstaged_changes.iter_change_type("A"):
            if diff_file.a_path.endswith(latest_version_substring):
                logger.error(
                    "FAKE_FILE_IN_GIT_STATUS detected! Please run `delete_fake_files` and regenerate documents."
                )
                raise SystemExit
            skipped_files.append(diff_file.a_path)

        file_path_reflections: dict[str, str] = {}
        for diff_file in itertools.chain(
            unstaged_changes.iter_change_type("M"),
            unstaged_changes.iter_change_type("D"),
        ):
            if diff_file.a_path.endswith(latest_version_substring):
                logger.error(
                    "FAKE_FILE_IN_GIT_STATUS detected! Please run `delete_fake_files` and regenerate documents."
                )
                raise SystemExit

            now_file_path = diff_file.a_path
            if now_file_path.endswith(".py"):
                raw_file_content = diff_file.a_blob.data_stream.read().decode("utf-8")
                latest_file_path = now_file_path[:-3] + latest_version_substring

                absolute_now = self.repo_path / now_file_path
                absolute_latest = self.repo_path / latest_file_path

                if absolute_now.exists():
                    os.rename(absolute_now, absolute_latest)
                    print(
                        f"{Fore.LIGHTMAGENTA_EX}[Save Latest Version of Code]: {Style.RESET_ALL}{now_file_path} -> {latest_file_path}"
                    )
                else:
                    print(
                        f"{Fore.LIGHTMAGENTA_EX}[Create Temp-File for Deleted (Not Staged) Files]: {Style.RESET_ALL}{now_file_path} -> {latest_file_path}"
                    )
                    with open(absolute_latest, "w", encoding="utf-8") as writer:
                        pass

                with open(absolute_now, "w", encoding="utf-8") as writer:
                    writer.write(raw_file_content)

                file_path_reflections[now_file_path] = latest_file_path

        return file_path_reflections, skipped_files

    # ------------------------------------------------------------------
    def delete_temp_versions(self) -> None:
        """Remove or restore temporary files created by `make_temp_versions`."""

        def _delete_recursively(filepath: Path) -> None:
            for entry in os.listdir(filepath):
                fi_d = filepath / entry
                if fi_d.is_dir():
                    _delete_recursively(fi_d)
                elif str(fi_d).endswith(latest_version_substring):
                    origin_name = Path(str(fi_d).replace(latest_version_substring, ".py"))
                    if origin_name.exists():
                        os.remove(origin_name)
                    if fi_d.stat().st_size == 0:
                        print(
                            f"{Fore.LIGHTRED_EX}[Deleting Temp File]: {Style.RESET_ALL}{fi_d.relative_to(self.repo_path)}, {origin_name.relative_to(self.repo_path)}"
                        )
                        os.remove(fi_d)
                    else:
                        print(
                            f"{Fore.LIGHTRED_EX}[Recovering Latest Version]: {Style.RESET_ALL}{origin_name.relative_to(self.repo_path)} <- {fi_d.relative_to(self.repo_path)}"
                        )
                        os.rename(fi_d, origin_name)

        _delete_recursively(self.repo_path)


def make_fake_files() -> tuple[dict[str, str], list[str]]:
    """Convenience wrapper around :class:`FakeFileManager`."""
    return FakeFileManager().make_temp_versions()


def delete_fake_files() -> None:
    """Convenience wrapper around :class:`FakeFileManager`."""
    FakeFileManager().delete_temp_versions()
