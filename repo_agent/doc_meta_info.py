"""存储doc对应的信息，同时处理引用的关系"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from colorama import Fore, Style
from prettytable import PrettyTable
from tqdm import tqdm

from repo_agent.file_handler import FileHandler
from repo_agent.log import logger
from repo_agent.multi_task_dispatch import Task, TaskManager
from repo_agent.settings import SettingsManager
from repo_agent.utils.meta_info_utils import latest_version_substring
from repo_agent.doc_items import DocItem, DocItemStatus, DocItemType, need_to_generate
from repo_agent.tree_builder import from_project_hierarchy_json
from repo_agent.reference_resolver import parse_reference




@dataclass
class MetaInfo:
    repo_path: Path = ""  # type: ignore
    document_version: str = ""
    target_repo_hierarchical_tree: DocItem = field(default_factory=lambda: DocItem())
    white_list: Any[List] = None

    fake_file_reflection: Dict[str, str] = field(default_factory=dict)
    jump_files: List[str] = field(default_factory=list)
    deleted_items_from_older_meta: List[List] = field(default_factory=list)

    in_generation_process: bool = False
    checkpoint_lock: threading.Lock = threading.Lock()

    @staticmethod
    def init_meta_info(file_path_reflections, jump_files) -> MetaInfo:
        """
        从一个仓库path中初始化metainfo
        """
        setting = SettingsManager.get_setting()
        project_abs_path = setting.project.target_repo

        print(f"{Fore.LIGHTRED_EX}Initializing MetaInfo: {Style.RESET_ALL}from {project_abs_path}")

        file_handler = FileHandler(project_abs_path, None)
        repo_structure = file_handler.generate_overall_structure(file_path_reflections, jump_files)
        metainfo = MetaInfo.from_project_hierarchy_json(repo_structure)
        metainfo.repo_path = project_abs_path
        metainfo.fake_file_reflection = file_path_reflections
        metainfo.jump_files = jump_files
        return metainfo

    @staticmethod
    def from_checkpoint_path(checkpoint_dir_path: Path) -> MetaInfo:
        """
        从已有的metainfo目录中读取
        """
        setting = SettingsManager.get_setting()
        project_hierarchy_json_path = checkpoint_dir_path / "project_hierarchy.json"

        with open(project_hierarchy_json_path, "r", encoding="utf-8") as reader:
            project_hierarchy_json = json.load(reader)
        metainfo = MetaInfo.from_project_hierarchy_json(project_hierarchy_json)

        with open(checkpoint_dir_path / "meta-info.json", "r", encoding="utf-8") as reader:
            meta_data = json.load(reader)
            metainfo.repo_path = setting.project.target_repo
            metainfo.document_version = meta_data["doc_version"]
            metainfo.fake_file_reflection = meta_data["fake_file_reflection"]
            metainfo.jump_files = meta_data["jump_files"]
            metainfo.in_generation_process = meta_data["in_generation_process"]
            metainfo.deleted_items_from_older_meta = meta_data["deleted_items_from_older_meta"]

        print(f"{Fore.CYAN}Loading MetaInfo:{Style.RESET_ALL} {checkpoint_dir_path}")
        return metainfo

    def checkpoint(self, target_dir_path: str | Path, flash_reference_relation=False):
        """
        Save the MetaInfo object to the specified directory.
        """
        with self.checkpoint_lock:
            target_dir = Path(target_dir_path)
            logger.debug(f"Checkpointing MetaInfo to directory: {target_dir}")
            print(f"{Fore.GREEN}MetaInfo is Refreshed and Saved{Style.RESET_ALL}")

            if not target_dir.exists():
                target_dir.mkdir(parents=True, exist_ok=True)
                logger.debug(f"Created directory: {target_dir}")

            # 保存 project_hierarchy.json 文件
            now_hierarchy_json = self.to_hierarchy_json(flash_reference_relation=flash_reference_relation)
            hierarchy_file = target_dir / "project_hierarchy.json"
            try:
                with hierarchy_file.open("w", encoding="utf-8") as writer:
                    json.dump(now_hierarchy_json, writer, indent=2, ensure_ascii=False)
                logger.debug(f"Saved hierarchy JSON to {hierarchy_file}")
            except IOError as e:
                logger.error(f"Failed to save hierarchy JSON to {hierarchy_file}: {e}")

            # 保存 meta-info.json 文件
            meta_info_file = target_dir / "meta-info.json"
            meta = {
                "doc_version": self.document_version,
                "in_generation_process": self.in_generation_process,
                "fake_file_reflection": self.fake_file_reflection,
                "jump_files": self.jump_files,
                "deleted_items_from_older_meta": self.deleted_items_from_older_meta,
            }
            try:
                with meta_info_file.open("w", encoding="utf-8") as writer:
                    json.dump(meta, writer, indent=2, ensure_ascii=False)
                logger.debug(f"Saved meta-info JSON to {meta_info_file}")
            except IOError as e:
                logger.error(f"Failed to save meta-info JSON to {meta_info_file}: {e}")

    def print_task_list(self, task_dict: Dict[Task]):
        """
        打印任务列表
        """
        task_table = PrettyTable(["task_id", "Doc Generation Reason", "Path", "dependency"])
        for task_id, task_info in task_dict.items():
            remain_str = "None"
            if task_info.dependencies:
                remain_str = ",".join(str(d_task.task_id) for d_task in task_info.dependencies)
                if len(remain_str) > 20:
                    remain_str = remain_str[:8] + "..." + remain_str[-8:]
            task_table.add_row([
                task_id,
                task_info.extra_info.item_status.name,
                task_info.extra_info.get_full_name(strict=True),
                remain_str,
            ])
        print(task_table)

    def get_all_files(self) -> List[DocItem]:
        """
        获取所有的file节点
        """
        files = []

        def walk_tree(node: DocItem):
            if node.item_type == DocItemType._file:
                files.append(node)
            for child in node.children.values():
                walk_tree(child)

        walk_tree(self.target_repo_hierarchical_tree)
        return files

    def find_obj_with_lineno(self, file_node: DocItem, start_line_num: int) -> DocItem:
        """
        根据起始行号在file_node树中查找对应的DocItem。
        """
        current_item = file_node
        while current_item.children:
            found_child = False
            for child in current_item.children.values():
                if (child.content["code_start_line"] <= start_line_num <= child.content["code_end_line"]):
                    current_item = child
                    found_child = True
                    break
            if not found_child:
                return current_item
        return current_item

    def parse_reference(self):
        """Delegate to :func:`reference_resolver.parse_reference`."""
        parse_reference(self)
    def get_task_manager(self, current_item: DocItem, task_available_func) -> TaskManager:
        """
        先写一个退化的版本，只考虑拓扑引用关系
        """
        doc_items = current_item.get_travel_list()

        # If there's a white_list, filter out anything not in the white list
        if self.white_list:
            def in_white_list(item: DocItem):
                for cont in self.white_list:
                    if item.get_file_name() == cont["file_path"] and item.obj_name == cont["id_text"]:
                        return True
                return False
            doc_items = list(filter(in_white_list, doc_items))

        doc_items = list(filter(task_available_func, doc_items))
        doc_items.sort(key=lambda x: x.depth)

        deal_items = []
        task_manager = TaskManager()

        bar = tqdm(total=len(doc_items), desc="parsing topology task-list")

        while doc_items:
            min_break_level = 1e7
            target_item = None

            for item in doc_items:
                best_break_level = 0
                second_best_break_level = 0

                # Depend on children if they're also in the doc_items
                for child in item.children.values():
                    if task_available_func(child) and (child not in deal_items):
                        best_break_level += 1

                # Depend on referenced items if they're also in doc_items
                for (referenced, special) in zip(item.reference_who, item.special_reference_type):
                    if task_available_func(referenced) and (referenced not in deal_items):
                        best_break_level += 1
                    if task_available_func(referenced) and not special and (referenced not in deal_items):
                        second_best_break_level += 1

                # If best_break_level is 0, we can pick this item right away
                if best_break_level == 0:
                    min_break_level = -1
                    target_item = item
                    break

                # Otherwise, track the minimal second-best
                if second_best_break_level < min_break_level:
                    target_item = item
                    min_break_level = second_best_break_level

            if min_break_level > 0 and target_item:
                print(f"circle-reference(second-best still failed), level={min_break_level}: {target_item.get_full_name()}")

            item_deps = []

            # Collect dependencies from children
            for child in target_item.children.values():
                if child.multithread_task_id != -1:
                    item_deps.append(child.multithread_task_id)

            # Collect dependencies from references
            for referenced_item in target_item.reference_who:
                if referenced_item.multithread_task_id in task_manager.task_dict:
                    item_deps.append(referenced_item.multithread_task_id)

            item_deps = list(set(item_deps))  # 去重

            if task_available_func(target_item):
                task_id = task_manager.add_task(dependency_task_ids=item_deps, extra=target_item)
                target_item.multithread_task_id = task_id

            deal_items.append(target_item)
            doc_items.remove(target_item)
            bar.update(1)

        return task_manager

    def get_topology(self, task_available_func) -> TaskManager:
        """
        计算repo中所有对象的拓扑顺序
        """
        self.parse_reference()
        return self.get_task_manager(self.target_repo_hierarchical_tree, task_available_func=task_available_func)

    def _map(self, deal_func: Callable):
        """
        对所有节点执行同一个操作
        """
        def traverse(item: DocItem):
            deal_func(item)
            for child in item.children.values():
                traverse(child)

        traverse(self.target_repo_hierarchical_tree)

    def load_doc_from_older_meta(self, older_meta: MetaInfo):
        """
        从一个较老的已生成文档的MetaInfo中继承文档信息
        """
        logger.info("merge doc from an older version of metainfo")
        root_item = self.target_repo_hierarchical_tree
        deleted_items = []

        def find_item(old_item: DocItem) -> Optional[DocItem]:
            if old_item.father is None:  # The root node
                return root_item

            father_result = find_item(old_item.father)
            if not father_result:
                return None

            real_name = None
            for child_real_name, temp_item in old_item.father.children.items():
                if temp_item == old_item:
                    real_name = child_real_name
                    break

            assert real_name is not None
            if real_name in father_result.children:
                return father_result.children[real_name]
            return None

        def travel(old_item: DocItem):
            result_item = find_item(old_item)
            if not result_item:
                deleted_items.append([old_item.get_full_name(), old_item.item_type.name])
                return

            result_item.md_content = old_item.md_content
            result_item.item_status = old_item.item_status

            if "code_content" in old_item.content:
                assert "code_content" in result_item.content
                if old_item.content["code_content"] != result_item.content["code_content"]:
                    result_item.item_status = DocItemStatus.code_changed

            for child in old_item.children.values():
                travel(child)

        travel(older_meta.target_repo_hierarchical_tree)

        # Re-parse references, then compare who_reference_me changes
        self.parse_reference()

        def travel2(old_item: DocItem):
            result_item = find_item(old_item)
            if not result_item:
                return

            new_ref_names = [x.get_full_name(strict=True) for x in result_item.who_reference_me]
            old_ref_names = old_item.who_reference_me_name_list

            if set(new_ref_names) != set(old_ref_names) and result_item.item_status == DocItemStatus.doc_up_to_date:
                if set(new_ref_names) <= set(old_ref_names):
                    result_item.item_status = DocItemStatus.referencer_not_exist
                else:
                    result_item.item_status = DocItemStatus.add_new_referencer

            for child in old_item.children.values():
                travel2(child)

        travel2(older_meta.target_repo_hierarchical_tree)
        self.deleted_items_from_older_meta = deleted_items

    @staticmethod
    def from_project_hierarchy_path(repo_path: str) -> MetaInfo:
        """
        从项目目录中读取 project_hierarchy.json，然后解析出 MetaInfo
        """
        project_hierarchy_json_path = os.path.join(repo_path, "project_hierarchy.json")
        logger.info(f"parsing from {project_hierarchy_json_path}")
        if not os.path.exists(project_hierarchy_json_path):
            raise NotImplementedError("Invalid operation detected")

        with open(project_hierarchy_json_path, "r", encoding="utf-8") as reader:
            project_hierarchy_json = json.load(reader)
        return MetaInfo.from_project_hierarchy_json(project_hierarchy_json)

    def to_hierarchy_json(self, flash_reference_relation=False):
        """
        Convert the document metadata to a hierarchical JSON representation.
        """
        hierachy_json = {}
        file_item_list = self.get_all_files()

        for file_item in file_item_list:
            file_hierarchy_content = []

            def walk_file(node: DocItem):
                temp_json_obj = node.content
                temp_json_obj["name"] = node.obj_name
                temp_json_obj["type"] = node.item_type.to_str()
                temp_json_obj["md_content"] = node.md_content
                temp_json_obj["item_status"] = node.item_status.name

                if flash_reference_relation:
                    temp_json_obj["who_reference_me"] = [
                        ref.get_full_name(strict=True) for ref in node.who_reference_me
                    ]
                    temp_json_obj["reference_who"] = [
                        ref.get_full_name(strict=True) for ref in node.reference_who
                    ]
                    temp_json_obj["special_reference_type"] = node.special_reference_type
                else:
                    temp_json_obj["who_reference_me"] = node.who_reference_me_name_list
                    temp_json_obj["reference_who"] = node.reference_who_name_list

                file_hierarchy_content.append(temp_json_obj)

                for child in node.children.values():
                    walk_file(child)

            for child in file_item.children.values():
                walk_file(child)

            hierachy_json[file_item.get_full_name()] = file_hierarchy_content

        return hierachy_json

    @staticmethod
    def from_project_hierarchy_json(project_hierarchy_json) -> "MetaInfo":
        """Create :class:`MetaInfo` from ``project_hierarchy.json``."""
        return from_project_hierarchy_json(project_hierarchy_json)


if __name__ == "__main__":
    repo_path = "some_repo_path"
    meta = MetaInfo.from_project_hierarchy_json(repo_path)
    meta.target_repo_hierarchical_tree.print_recursive()
    topology_list = meta.get_topology()
