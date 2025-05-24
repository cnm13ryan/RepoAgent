"""Functions used to analyse reference relationships between nodes."""

from __future__ import annotations

import os
from typing import List

import jedi
from colorama import Fore, Style
from tqdm import tqdm

from repo_agent.log import logger
from repo_agent.doc_items import DocItem, DocItemType


def find_all_referencer(
    repo_path: str,
    variable_name: str,
    file_path: str,
    line_number: int,
    column_number: int,
    in_file_only: bool = False,
) -> List[tuple[str, int, int]]:
    """Return positions that reference ``variable_name`` using ``jedi``."""
    script = jedi.Script(path=os.path.join(repo_path, file_path))
    try:
        if in_file_only:
            references = script.get_references(line=line_number, column=column_number, scope="file")
        else:
            references = script.get_references(line=line_number, column=column_number)
        variable_references = [ref for ref in references if ref.name == variable_name]
        return [
            (os.path.relpath(ref.module_path, repo_path), ref.line, ref.column)
            for ref in variable_references
            if not (ref.line == line_number and ref.column == column_number)
        ]
    except Exception as e:  # pragma: no cover - jedi internal errors are not deterministic
        logger.error(f"Error occurred: {e}")
        logger.error(
            f"Parameters: variable_name={variable_name}, file_path={file_path}, line_number={line_number}, column_number={column_number}"
        )
        return []


def parse_reference(meta_info: "MetaInfo") -> None:
    """Populate bidirectional reference links inside ``meta_info``."""
    file_nodes = meta_info.get_all_files()

    white_list_file_names: List[str] = []
    white_list_obj_names: List[str] = []
    if meta_info.white_list is not None:
        white_list_file_names = [cont["file_path"] for cont in meta_info.white_list]
        white_list_obj_names = [cont["id_text"] for cont in meta_info.white_list]

    for file_node in tqdm(file_nodes, desc="parsing bidirectional reference"):
        rel_file_path = file_node.get_full_name()
        if rel_file_path in meta_info.jump_files:
            continue
        if white_list_file_names and (file_node.get_file_name() not in white_list_file_names):
            continue
        ref_count = 0

        def walk_file(current_item: DocItem) -> None:
            nonlocal ref_count
            in_file_only = False
            if white_list_obj_names and (current_item.obj_name not in white_list_obj_names):
                in_file_only = True
            reference_list = find_all_referencer(
                repo_path=meta_info.repo_path,
                variable_name=current_item.obj_name,
                file_path=rel_file_path,
                line_number=current_item.content["code_start_line"],
                column_number=current_item.content["name_column"],
                in_file_only=in_file_only,
            )
            for referencer_pos in reference_list:
                referencer_file_ral_path = referencer_pos[0]
                if referencer_file_ral_path in meta_info.fake_file_reflection.values():
                    print(
                        f"{Fore.LIGHTBLUE_EX}[Reference From Unstaged Version, skip]{Style.RESET_ALL} {referencer_file_ral_path} -> {current_item.get_full_name()}"
                    )
                    continue
                if referencer_file_ral_path in meta_info.jump_files:
                    print(
                        f"{Fore.LIGHTBLUE_EX}[Reference From Untracked Version, skip]{Style.RESET_ALL} {referencer_file_ral_path} -> {current_item.get_full_name()}"
                    )
                    continue
                target_file_hiera = referencer_file_ral_path.split("/")
                referencer_file_item = meta_info.target_repo_hierarchical_tree.find(target_file_hiera)
                if referencer_file_item is None:
                    print(
                        f'{Fore.LIGHTRED_EX}Error: Find "{referencer_file_ral_path}"(not in target repo){Style.RESET_ALL} referenced {current_item.get_full_name()}'
                    )
                    continue
                referencer_node = meta_info.find_obj_with_lineno(referencer_file_item, referencer_pos[1])
                if referencer_node.obj_name == current_item.obj_name:
                    logger.info(
                        f"Jedi find {current_item.get_full_name()} with name_duplicate_reference, skipped"
                    )
                    continue
                if DocItem.has_ans_relation(current_item, referencer_node) is None:
                    if current_item not in referencer_node.reference_who:
                        special_reference = (
                            referencer_node.item_type
                            in [DocItemType._function, DocItemType._sub_function, DocItemType._class_function]
                        ) and (referencer_node.code_start_line == referencer_pos[1])
                        referencer_node.special_reference_type.append(special_reference)
                        referencer_node.reference_who.append(current_item)
                        current_item.who_reference_me.append(referencer_node)
                        ref_count += 1
            for child in current_item.children.values():
                walk_file(child)

        for child in file_node.children.values():
            walk_file(child)
