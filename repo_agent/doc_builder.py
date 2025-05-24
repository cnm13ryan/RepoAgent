from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

from tqdm import tqdm

from repo_agent.log import logger


class MarkdownBuilder:
    """Convert ``MetaInfo`` data into Markdown files."""

    def __init__(self, settings, lock: threading.Lock | None = None):
        self.settings = settings
        self.lock = lock or threading.Lock()

    def refresh(self, meta_info) -> None:
        """Rebuild all Markdown documentation from ``meta_info``."""
        markdown_folder = (
            Path(self.settings.project.target_repo)
            / self.settings.project.markdown_docs_name
        )

        with self.lock:
            if markdown_folder.exists():
                logger.debug(f"Deleting existing contents of {markdown_folder}")
                shutil.rmtree(markdown_folder)
            markdown_folder.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Created markdown folder at {markdown_folder}")

        file_item_list = meta_info.get_all_files()
        logger.debug(f"Found {len(file_item_list)} files to process.")

        for file_item in tqdm(file_item_list):
            if not self._has_doc(file_item):
                logger.debug(
                    f"No documentation content for: {file_item.get_full_name()}, skipping."
                )
                continue

            markdown = "".join(
                self.to_markdown(child, 2) for child in file_item.children.values()
            )
            if not markdown:
                logger.warning(
                    f"No markdown content generated for: {file_item.get_full_name()}"
                )
                continue

            file_path = (
                Path(self.settings.project.markdown_docs_name)
                / file_item.get_file_name().replace(".py", ".md")
            )
            abs_file_path = self.settings.project.target_repo / file_path
            logger.debug(f"Writing markdown to: {abs_file_path}")

            abs_file_path.parent.mkdir(parents=True, exist_ok=True)

            with self.lock:
                for attempt in range(3):
                    try:
                        with open(abs_file_path, "w", encoding="utf-8") as file:
                            file.write(markdown)
                        logger.debug(f"Successfully wrote to {abs_file_path}")
                        break
                    except IOError as e:
                        logger.error(
                            f"Failed to write {abs_file_path} on attempt {attempt + 1}: {e}"
                        )
                        time.sleep(1)

        logger.info(
            f"Markdown documents have been refreshed at {self.settings.project.markdown_docs_name}"
        )

    def _has_doc(self, item) -> bool:
        if item.md_content:
            return True
        return any(self._has_doc(child) for child in item.children.values())

    def to_markdown(self, item, level: int) -> str:
        """Recursively convert ``item`` into Markdown text."""
        md = "#" * level + f" {item.item_type.to_str()} {item.obj_name}"
        if "params" in item.content.keys() and item.content["params"]:
            md += f"({', '.join(item.content['params'])})"
        md += "\n"
        if item.md_content:
            md += f"{item.md_content[-1]}\n"
        else:
            md += "Doc is waiting to be generated...\n"
        for child in item.children.values():
            md += self.to_markdown(child, level + 1)
            md += "***\n"
        return md

