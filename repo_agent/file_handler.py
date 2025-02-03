import ast
import json
import os

import git
from colorama import Fore, Style
from tqdm import tqdm

from repo_agent.log import logger
from repo_agent.settings import SettingsManager
from repo_agent.utils.gitignore_checker import GitignoreChecker
from repo_agent.utils.meta_info_utils import latest_verison_substring


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

    def get_obj_code_info(
        self, code_type, code_name, start_line, end_line, params, file_path=None
    ):
        """
        Get the code information for a given object (function/class/async).

        Args:
            code_type (str): The type of the code ('FunctionDef', 'ClassDef', etc.).
            code_name (str): The name of the code object.
            start_line (int): The starting line number of the code.
            end_line (int): The ending line number of the code.
            params (list): List of parameter names.
            file_path (str, optional): Relative file path. Defaults to None.

        Returns:
            dict: A dictionary containing code details (type, name, content, etc.).
        """

        code_info = {}
        code_info["type"] = code_type
        code_info["name"] = code_name
        code_info["md_content"] = []
        code_info["code_start_line"] = start_line
        code_info["code_end_line"] = end_line
        code_info["params"] = params

        target_path = file_path if file_path is not None else self.file_path
        full_path = os.path.join(self.repo_path, target_path)

        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            code_content = "".join(lines[start_line - 1:end_line])

            # Find the object's name position in the first line
            name_column = lines[start_line - 1].find(code_name)

            # Check if there's a "return" in the code block
            if "return" in code_content:
                has_return = True
            else:
                has_return = False

            code_info["has_return"] = has_return
            code_info["code_content"] = code_content
            code_info["name_column"] = name_column

        return code_info

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

    def get_end_lineno(self, node):
        """
        Recursively find the maximum end_lineno among a node and its children.

        Args:
            node (ast.AST): The AST node.

        Returns:
            int: The final end line number. -1 if not applicable.
        """
        if not hasattr(node, "lineno"):
            return -1

        end_lineno = node.lineno
        for child in ast.iter_child_nodes(node):
            child_end = getattr(child, "end_lineno", None) or self.get_end_lineno(child)
            if child_end > -1:
                end_lineno = max(end_lineno, child_end)
        return end_lineno

    def add_parent_references(self, node, parent=None):
        """
        Recursively add 'parent' references to each child node in the AST.

        Args:
            node (ast.AST): The current AST node.
            parent (ast.AST, optional): The parent node. Defaults to None.
        """
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.add_parent_references(child, node)

    def get_functions_and_classes(self, code_content):
        """
        Retrieves functions/classes/async functions from the AST.

        Returns:
            list: A list of tuples (type, name, start_line, end_line, params).
        """
        tree = ast.parse(code_content)
        self.add_parent_references(tree)

        functions_and_classes = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                start_line = node.lineno
                end_line = self.get_end_lineno(node)
                parameters = (
                    [arg.arg for arg in node.args.args] if "args" in dir(node) else []
                )
                functions_and_classes.append(
                    (type(node).__name__, node.name, start_line, end_line, parameters)
                )

        return functions_and_classes

    def generate_file_structure(self, file_path):
        """
        Parse a file via AST and collect code objects' info.

        Args:
            file_path (str): Relative path of the file.

        Returns:
            list: A list of dicts with info about each found code object.
        """
        content = self._read_file_content(file_path)
        structures = self.get_functions_and_classes(content)

        file_objects = []
        for struct in structures:
            structure_type, name, start_line, end_line, params = struct
            code_info = self.get_obj_code_info(
                structure_type, name, start_line, end_line, params, file_path
            )
            file_objects.append(code_info)

        return file_objects

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
            elif not_ignored_files.endswith(latest_verison_substring):
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
        """
        Convert the file structure (as stored in project_hierarchy.json) to a markdown outline.

        Args:
            file_path (str, optional): The relative path of the file to be converted.

        Returns:
            str: The content in markdown format.

        Raises:
            ValueError: If no corresponding entry is found in project_hierarchy.json.
        """
        with open(self.project_hierarchy, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        if file_path is None:
            file_path = self.file_path

        file_dict = json_data.get(file_path)
        if file_dict is None:
            raise ValueError(
                f"No file object found for {self.file_path} in project_hierarchy.json"
            )

        markdown = ""
        parent_dict = {}
        objects = sorted(file_dict.values(), key=lambda obj: obj["code_start_line"])

        for obj in objects:
            if obj["parent"] is not None:
                parent_dict[obj["name"]] = obj["parent"]

        current_parent = None
        for obj in objects:
            level = 1
            parent = obj["parent"]
            while parent is not None:
                level += 1
                parent = parent_dict.get(parent)

            if level == 1 and current_parent is not None:
                markdown += "***\n"
            current_parent = obj["name"]

            params_str = ""
            if obj["type"] in ["FunctionDef", "AsyncFunctionDef"]:
                params_str = "()"
                if obj["params"]:
                    params_str = f"({', '.join(obj['params'])})"

            markdown += f"{'#' * level} {obj['type']} {obj['name']}{params_str}:\n"
            markdown += f"{obj['md_content'][-1] if len(obj['md_content']) > 0 else ''}\n"

        markdown += "***\n"
        return markdown
