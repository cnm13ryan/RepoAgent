import json

from repo_agent.settings import SettingsManager


class MarkdownWriter:
    """Generate Markdown documentation from project hierarchy data."""

    def __init__(self) -> None:
        setting = SettingsManager.get_setting()
        self.project_hierarchy = (
            setting.project.target_repo / setting.project.hierarchy_name
        )

    def convert_to_markdown_file(self, file_path: str) -> str:
        """Convert a single file entry from ``project_hierarchy.json`` to Markdown."""
        with open(self.project_hierarchy, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        file_dict = json_data.get(file_path)
        if file_dict is None:
            raise ValueError(
                f"No file object found for {file_path} in project_hierarchy.json"
            )

        markdown = ""
        parent_dict: dict[str, str | None] = {}
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
