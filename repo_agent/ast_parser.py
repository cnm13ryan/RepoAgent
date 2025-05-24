import ast
import os


class ASTParser:
    """Parse Python files and extract structure information."""

    def __init__(self, repo_path: str) -> None:
        self.repo_path = repo_path

    def _read_file_content(self, relative_path: str) -> str:
        """Return file content from a path relative to repository root."""
        abs_path = os.path.join(self.repo_path, relative_path)
        with open(abs_path, "r", encoding="utf-8") as f:
            return f.read()

    def get_end_lineno(self, node: ast.AST) -> int:
        """Recursively find the maximum ``end_lineno`` among a node and its children."""
        if not hasattr(node, "lineno"):
            return -1

        end_lineno = node.lineno
        for child in ast.iter_child_nodes(node):
            child_end = getattr(child, "end_lineno", None) or self.get_end_lineno(child)
            if child_end > -1:
                end_lineno = max(end_lineno, child_end)
        return end_lineno

    def add_parent_references(self, node: ast.AST, parent: ast.AST | None = None) -> None:
        """Add ``parent`` references to all nodes in the AST tree."""
        for child in ast.iter_child_nodes(node):
            child.parent = node
            self.add_parent_references(child, node)

    def get_functions_and_classes(self, code_content: str) -> list[tuple]:
        """Return ``(type, name, start, end, params)`` for functions and classes."""
        tree = ast.parse(code_content)
        self.add_parent_references(tree)

        results: list[tuple] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                start_line = node.lineno
                end_line = self.get_end_lineno(node)
                parameters = [arg.arg for arg in node.args.args] if "args" in dir(node) else []
                results.append((type(node).__name__, node.name, start_line, end_line, parameters))
        return results

    def get_obj_code_info(
        self,
        code_type: str,
        code_name: str,
        start_line: int,
        end_line: int,
        params: list[str],
        file_path: str,
    ) -> dict:
        """Collect detailed information about a code object."""
        code_info: dict = {
            "type": code_type,
            "name": code_name,
            "md_content": [],
            "code_start_line": start_line,
            "code_end_line": end_line,
            "params": params,
        }

        full_path = os.path.join(self.repo_path, file_path)
        with open(full_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            code_content = "".join(lines[start_line - 1:end_line])
            name_column = lines[start_line - 1].find(code_name)
            has_return = "return" in code_content

        code_info["has_return"] = has_return
        code_info["code_content"] = code_content
        code_info["name_column"] = name_column
        return code_info

    def generate_file_structure(self, file_path: str) -> list[dict]:
        """Parse a file and return a list of code object dictionaries."""
        content = self._read_file_content(file_path)
        structures = self.get_functions_and_classes(content)

        file_objects: list[dict] = []
        for struct in structures:
            structure_type, name, start_line, end_line, params = struct
            code_info = self.get_obj_code_info(
                structure_type, name, start_line, end_line, params, file_path
            )
            file_objects.append(code_info)
        return file_objects
