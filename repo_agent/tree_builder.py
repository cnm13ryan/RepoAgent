"""Utilities to construct the documentation tree from raw data."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from tqdm import tqdm

from repo_agent.log import logger
from repo_agent.settings import SettingsManager
from repo_agent.doc_items import DocItem, DocItemStatus, DocItemType


def from_project_hierarchy_json(project_hierarchy_json: Dict[str, List[Dict]]) -> "MetaInfo":
    """Build a ``MetaInfo`` object from ``project_hierarchy.json`` content."""
    from repo_agent.doc_meta_info import MetaInfo

    setting = SettingsManager.get_setting()
    target_meta_info = MetaInfo(
        target_repo_hierarchical_tree=DocItem(
            item_type=DocItemType._repo,
            obj_name="full_repo",
        )
    )

    for file_name, file_content in tqdm(project_hierarchy_json.items(), desc="parsing parent relationship"):
        full_path = os.path.join(setting.project.target_repo, file_name)
        if not os.path.exists(full_path):
            logger.info(f"deleted content: {file_name}")
            continue
        if os.path.getsize(full_path) == 0:
            logger.info(f"blank content: {file_name}")
            continue

        recursive_file_path = file_name.split("/")
        now_structure = target_meta_info.target_repo_hierarchical_tree

        for pos in range(len(recursive_file_path) - 1):
            if recursive_file_path[pos] not in now_structure.children:
                now_structure.children[recursive_file_path[pos]] = DocItem(
                    item_type=DocItemType._dir,
                    md_content="",
                    obj_name=recursive_file_path[pos],
                )
                now_structure.children[recursive_file_path[pos]].father = now_structure
            now_structure = now_structure.children[recursive_file_path[pos]]

        if recursive_file_path[-1] not in now_structure.children:
            now_structure.children[recursive_file_path[-1]] = DocItem(
                item_type=DocItemType._file,
                obj_name=recursive_file_path[-1],
            )
            now_structure.children[recursive_file_path[-1]].father = now_structure

        file_item = target_meta_info.target_repo_hierarchical_tree.find(recursive_file_path)
        assert file_item.item_type == DocItemType._file

        obj_item_list: List[DocItem] = []
        for value in file_content:
            obj_doc_item = DocItem(
                obj_name=value["name"],
                content=value,
                md_content=value["md_content"],
                code_start_line=value["code_start_line"],
                code_end_line=value["code_end_line"],
            )
            if "item_status" in value:
                obj_doc_item.item_status = DocItemStatus[value["item_status"]]
            if "reference_who" in value:
                obj_doc_item.reference_who_name_list = value["reference_who"]
            if "special_reference_type" in value:
                obj_doc_item.special_reference_type = value["special_reference_type"]
            if "who_reference_me" in value:
                obj_doc_item.who_reference_me_name_list = value["who_reference_me"]
            obj_item_list.append(obj_doc_item)

        for item in obj_item_list:
            potential_father: Optional[DocItem] = None

            def code_contain(child_item: DocItem, parent_candidate: DocItem) -> bool:
                if (
                    parent_candidate.code_end_line == child_item.code_end_line
                    and parent_candidate.code_start_line == child_item.code_start_line
                ):
                    return False
                if (
                    parent_candidate.code_end_line < child_item.code_end_line
                    or parent_candidate.code_start_line > child_item.code_start_line
                ):
                    return False
                return True

            for other_item in obj_item_list:
                if code_contain(item, other_item):
                    if potential_father is None or (
                        (other_item.code_end_line - other_item.code_start_line)
                        < (potential_father.code_end_line - potential_father.code_start_line)
                    ):
                        potential_father = other_item

            if potential_father is None:
                potential_father = file_item

            item.father = potential_father

            child_name = item.obj_name
            if child_name in potential_father.children:
                name_id = 0
                while (child_name + f"_{name_id}") in potential_father.children:
                    name_id += 1
                logger.warning(
                    f"Name duplicate in {file_item.get_full_name()}: rename to {item.obj_name}->{child_name}_{name_id}"
                )
                child_name += f"_{name_id}"

            potential_father.children[child_name] = item

        def change_items(node: DocItem) -> None:
            if node.item_type != DocItemType._file:
                if node.content["type"] == "ClassDef":
                    node.item_type = DocItemType._class
                elif node.content["type"] == "FunctionDef":
                    node.item_type = DocItemType._function
                    if node.father.item_type == DocItemType._class:
                        node.item_type = DocItemType._class_function
                    elif node.father.item_type in [DocItemType._function, DocItemType._sub_function]:
                        node.item_type = DocItemType._sub_function
            for c in node.children.values():
                change_items(c)

        change_items(file_item)

    target_meta_info.target_repo_hierarchical_tree.parse_tree_path(now_path=[])
    target_meta_info.target_repo_hierarchical_tree.check_depth()
    return target_meta_info
