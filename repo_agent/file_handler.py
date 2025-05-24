import os

import git
from colorama import Fore, Style
from tqdm import tqdm

from repo_agent.log import logger
from repo_agent.settings import SettingsManager
from repo_agent.utils.gitignore_checker import GitignoreChecker
from repo_agent.utils.meta_info_utils import latest_version_substring
from repo_agent.ast_parser import ASTParser
from repo_agent.markdown_writer import MarkdownWriter


class FileHandler:
    """
    A class for handling file operations (reading, writing, etc.).
    Typically used per changed file in the repository.
    """

    def __init__(self, repo_path, file_path):
        """
        Args:
            repo_path (str): Absolute path to the repository root.
            file_path (str): Relative path from repo root to the target file.
        """
        self.file_path = file_path
        self.repo_path = repo_path

        setting = SettingsManager.get_setting()
        self.project_hierarchy = (
            setting.project.target_repo / setting.project.hierarchy_name
        )

        self.ast_parser = ASTParser(repo_path)
        self.markdown_writer = MarkdownWriter()

    def _read_file_content(self, relative_path):
        """
        Safely read file content given a path relative to the repo root.
        Returns:
            str: The entire file content.
        """
        abs_path = os.path.join(self.repo_path, relative_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()

    def read_file(self):
        """
        Read the file content of the current file_path.

        Returns:
            str: The content of the current changed file.
        """
        return self._read_file_content(self.file_path)


    def write_file(self, file_path, content):
        """
        Write content to a file relative to the repo root.

        Args:
            file_path (str): The relative path of the file.
            content (str): The content to be written to the file.
        """
        if file_path.startswith("/"):
            file_path = file_path[1:]

        abs_file_path = os.path.join(self.repo_path, file_path)
        os.makedirs(os.path.dirname(abs_file_path), exist_ok=True)
        with open(abs_file_path, "w", encoding="utf-8") as file:
            file.write(content)

    def get_modified_file_versions(self):
        """
        Get the current and previous versions of the file.

        Returns:
            tuple: (current_version, previous_version) as strings.
        """
        repo = git.Repo(self.repo_path)

        # Current version
        current_version_path = os.path.join(self.repo_path, self.file_path)
        with open(current_version_path, "r", encoding="utf-8") as file:
            current_version = file.read()

        # Previous version from the last commit
        commits = list(repo.iter_commits(paths=self.file_path, max_count=1))
        previous_version = None
        if commits:
            commit = commits[0]
            try:
                previous_version = (
                    (commit.tree / self.file_path).data_stream.read().decode("utf-8")
                )
            except KeyError:
                # File may be newly added
                previous_version = None

        return current_version, previous_version

    def generate_file_structure(self, file_path):
        """Parse a file and collect code objects using :class:`ASTParser`."""
        return self.ast_parser.generate_file_structure(file_path)

    def generate_overall_structure(self, file_path_reflections, jump_files) -> dict:
        """
        Build a dictionary of file structures for all non-ignored files in the repo,
        skipping certain files if needed.

        Args:
            file_path_reflections (dict): Possibly a map of some shadow paths.
            jump_files (list): List of files to skip.

        Returns:
            dict: A mapping of filename -> list of code object info.
        """
        repo_structure = {}
        gitignore_checker = GitignoreChecker(
            directory=self.repo_path,
            gitignore_path=os.path.join(self.repo_path, ".gitignore"),
        )

        bar = tqdm(gitignore_checker.check_files_and_folders())
        for not_ignored_files in bar:
            normal_file_names = not_ignored_files

            if not_ignored_files in jump_files:
                logger.info(
                    f"{Fore.LIGHTYELLOW_EX}[File-Handler] Unstaged AddFile, ignore this file: {Style.RESET_ALL}{normal_file_names}"
                )
                continue
            elif not_ignored_files.endswith(latest_version_substring):
                logger.info(
                    f"{Fore.LIGHTYELLOW_EX}[File-Handler] Skip Latest Version, Using Git-Status Version]: {Style.RESET_ALL}{normal_file_names}"
                )
                continue

            try:
                repo_structure[normal_file_names] = self.generate_file_structure(
                    not_ignored_files
                )
            except Exception as e:
                logger.error(
                    f"Alert: An error occurred while generating file structure for {not_ignored_files}: {e}"
                )
                continue

            bar.set_description(f"generating repo structure: {not_ignored_files}")

        return repo_structure

    def convert_to_markdown_file(self, file_path=None):
        """Return Markdown outline for ``file_path`` using :class:`MarkdownWriter`."""
        target = file_path or self.file_path
        return self.markdown_writer.convert_to_markdown_file(target)
