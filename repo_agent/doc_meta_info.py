"""存储doc对应的信息，同时处理引用的关系"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from enum import Enum, auto, unique
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import jedi
from colorama import Fore, Style
from prettytable import PrettyTable
from tqdm import tqdm

from repo_agent.file_handler import FileHandler
from repo_agent.log import logger
from repo_agent.multi_task_dispatch import Task, TaskManager
from repo_agent.settings import SettingsManager
from repo_agent.utils.meta_info_utils import latest_verison_substring


@unique
class EdgeType(Enum):
    reference_edge = auto()  # 一个obj引用另一个obj
    subfile_edge = auto()    # 一个 文件/文件夹 属于一个文件夹
    file_item_edge = auto()  # 一个 obj 属于一个文件


@unique
class DocItemType(Enum):
    """
    对可能的对象文档类型进行定义（分不同细粒度）
    """
    _repo = auto()          # 根节点，需要生成readme
    _dir = auto()
    _file = auto()
    _class = auto()
    _class_function = auto()
    _function = auto()      # 文件内的常规function
    _sub_function = auto()  # function内定义的subfunction
    _global_var = auto()

    def to_str(self):
        # Convert enum member to a string representation used by the parser
        if self == DocItemType._class:
            return "ClassDef"
        elif self in [DocItemType._function, DocItemType._class_function, DocItemType._sub_function]:
            return "FunctionDef"
        return self.name

    def print_self(self):
        # Return a color-coded name for pretty printing
        color = Fore.WHITE
        if self == DocItemType._dir:
            color = Fore.GREEN
        elif self == DocItemType._file:
            color = Fore.YELLOW
        elif self == DocItemType._class:
            color = Fore.RED
        elif self in [
            DocItemType._function,
            DocItemType._sub_function,
            DocItemType._class_function,
        ]:
            color = Fore.BLUE
        return color + self.name + Style.RESET_ALL


@unique
class DocItemStatus(Enum):
    doc_up_to_date = auto()               # 无需生成文档
    doc_has_not_been_generated = auto()   # 文档还未生成，需要生成
    code_changed = auto()                 # 源码被修改，需要更新文档
    add_new_referencer = auto()           # 添加了新的引用者
    referencer_not_exist = auto()         # 曾经引用他的对象被删除或不再引用他了


def need_to_generate(doc_item: DocItem, ignore_list: List[str] = []) -> bool:
    """
    只生成item的文档，文件及更高粒度都跳过。
    如果属于一个黑名单文件也跳过。
    """
    if doc_item.item_status == DocItemStatus.doc_up_to_date:
        return False

    rel_file_path = doc_item.get_full_name()

    # Skip doc generation for file/dir/repo
    if doc_item.item_type in (DocItemType._file, DocItemType._dir, DocItemType._repo):
        return False

    parent = doc_item.father
    while parent:
        if parent.item_type == DocItemType._file:
            # If the file path is in the ignore list, skip
            if any(rel_file_path.startswith(ignore_item) for ignore_item in ignore_list):
                return False
            return True
        parent = parent.father

    return False


@dataclass(eq=False)
class DocItem:
    item_type: DocItemType = DocItemType._class_function
    item_status: DocItemStatus = DocItemStatus.doc_has_not_been_generated

    obj_name: str = ""
    code_start_line: int = -1
    code_end_line: int = -1
    md_content: List[str] = field(default_factory=list)         # 存储不同版本的doc
    content: Dict[Any, Any] = field(default_factory=dict)       # 原本存储的信息

    children: Dict[str, 'DocItem'] = field(default_factory=dict, compare=False)
    father: Optional['DocItem'] = field(default=None, compare=False)

    depth: int = 0
    tree_path: List['DocItem'] = field(default_factory=list, compare=False)
    max_reference_ansce: Optional['DocItem'] = field(default=None, compare=False)

    reference_who: List['DocItem'] = field(default_factory=list, compare=False)       # 他引用了谁
    who_reference_me: List['DocItem'] = field(default_factory=list, compare=False)    # 谁引用了他
    special_reference_type: List[bool] = field(default_factory=list)

    reference_who_name_list: List[str] = field(default_factory=list)
    who_reference_me_name_list: List[str] = field(default_factory=list)

    has_task: bool = False
    multithread_task_id: int = -1

    @staticmethod
    def has_ans_relation(a: DocItem, b: DocItem):
        """
        Check if there is an ancestor relationship between two nodes.
        Return whichever node is higher in the ancestor chain if found.
        Otherwise return None.
        """
        if b in a.tree_path:
            return b
        if a in b.tree_path:
            return a
        return None

    def get_travel_list(self):
        """
        Return a list of self + all descendants (pre-order traversal).
        """
        nodes = [self]
        for child in self.children.values():
            nodes.extend(child.get_travel_list())
        return nodes

    def check_depth(self):
        """
        Recursively calculate the depth of the node.
        """
        if len(self.children) == 0:
            self.depth = 0
            return 0
        max_child_depth = 0
        for child in self.children.values():
            child_depth = child.check_depth()
            max_child_depth = max(child_depth, max_child_depth)
        self.depth = max_child_depth + 1
        return self.depth

    def parse_tree_path(self, now_path):
        """
        Recursively build the path from the root to this node.
        """
        self.tree_path = now_path + [self]
        for child in self.children.values():
            child.parse_tree_path(self.tree_path)

    def get_file_name(self):
        full_name = self.get_full_name()
        return full_name.split(".py")[0] + ".py"

    def get_full_name(self, strict=False):
        """
        获取从下到上所有的obj名字, 以 / 分隔.
        If `strict=True`, handle potential naming conflicts by appending `(name_duplicate_version)`.
        """
        if self.father is None:
            return self.obj_name

        name_list = []
        current = self
        while current is not None:
            current_name = current.obj_name
            if strict:
                for name, item in current.father.children.items():
                    if item == current:
                        current_name = name
                        break
                if current_name != current.obj_name:
                    current_name += "(name_duplicate_version)"
            name_list.insert(0, current_name)
            current = current.father

        # The first node in the chain is the repo root; skip that name
        name_list = name_list[1:]
        return "/".join(name_list)

    def find(self, recursive_file_path: list) -> Optional[DocItem]:
        """
        From the repo root, follow the path to find a nested child file or object.
        Return None if not found.
        """
        assert self.item_type == DocItemType._repo
        pos = 0
        current_item = self
        while pos < len(recursive_file_path):
            if recursive_file_path[pos] not in current_item.children:
                return None
            current_item = current_item.children[recursive_file_path[pos]]
            pos += 1
        return current_item

    @staticmethod
    def check_has_task(current_item: DocItem, ignore_list: List[str] = []):
        """
        Mark doc items that need generation or whose children need generation.
        """
        if need_to_generate(current_item, ignore_list=ignore_list):
            current_item.has_task = True
        for child in current_item.children.values():
            DocItem.check_has_task(child, ignore_list)
            current_item.has_task = current_item.has_task or child.has_task

    def print_recursive(self, indent=0, print_content=False, diff_status=False, ignore_list: List[str] = []):
        """
        递归打印repo对象信息
        """
        def print_indent(ind=0):
            if ind == 0:
                return ""
            return "  " * ind + "|-"

        item_name = self.obj_name
        setting = SettingsManager.get_setting()

        # If this is the repo root, display the actual folder name
        if self.item_type == DocItemType._repo:
            item_name = setting.project.target_repo

        if diff_status and need_to_generate(self, ignore_list=ignore_list):
            print(f"{print_indent(indent)}{self.item_type.print_self()}: {item_name} : {self.item_status.name}")
        else:
            print(f"{print_indent(indent)}{self.item_type.print_self()}: {item_name}")

        for child_name, child in self.children.items():
            # If diff_status is True, skip children that have no tasks
            if diff_status and not child.has_task:
                continue
            child.print_recursive(
                indent=indent + 1,
                print_content=print_content,
                diff_status=diff_status,
                ignore_list=ignore_list,
            )


def find_all_referencer(repo_path, variable_name, file_path, line_number, column_number, in_file_only=False):
    """
    使用 jedi 查找对某个变量的引用位置。
    """
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
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        logger.error(
            f"Parameters: variable_name={variable_name}, file_path={file_path}, "
            f"line_number={line_number}, column_number={column_number}"
        )
        return []


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
        """
        双向提取所有引用关系
        """
        file_nodes = self.get_all_files()

        white_list_file_names = []
        white_list_obj_names = []
        if self.white_list is not None:
            white_list_file_names = [cont["file_path"] for cont in self.white_list]
            white_list_obj_names = [cont["id_text"] for cont in self.white_list]

        for file_node in tqdm(file_nodes, desc="parsing bidirectional reference"):
            rel_file_path = file_node.get_full_name()

            if rel_file_path in self.jump_files:
                # Skip files that are in jump_files
                continue

            if white_list_file_names and (file_node.get_file_name() not in white_list_file_names):
                # Skip files not in the white list (if white_list_file_names is set)
                continue

            ref_count = 0

            def walk_file(current_item: DocItem):
                nonlocal ref_count
                in_file_only = False

                # If there's a white list of obj_names, only parse references within file if not matched
                if white_list_obj_names and (current_item.obj_name not in white_list_obj_names):
                    in_file_only = True

                reference_list = find_all_referencer(
                    repo_path=self.repo_path,
                    variable_name=current_item.obj_name,
                    file_path=rel_file_path,
                    line_number=current_item.content["code_start_line"],
                    column_number=current_item.content["name_column"],
                    in_file_only=in_file_only,
                )

                for referencer_pos in reference_list:
                    referencer_file_ral_path = referencer_pos[0]

                    # Skip references from unstaged/fake or untracked files
                    if referencer_file_ral_path in self.fake_file_reflection.values():
                        print(f"{Fore.LIGHTBLUE_EX}[Reference From Unstaged Version, skip]{Style.RESET_ALL} {referencer_file_ral_path} -> {current_item.get_full_name()}")
                        continue
                    if referencer_file_ral_path in self.jump_files:
                        print(f"{Fore.LIGHTBLUE_EX}[Reference From Untracked Version, skip]{Style.RESET_ALL} {referencer_file_ral_path} -> {current_item.get_full_name()}")
                        continue

                    target_file_hiera = referencer_file_ral_path.split("/")
                    referencer_file_item = self.target_repo_hierarchical_tree.find(target_file_hiera)
                    if referencer_file_item is None:
                        print(f'{Fore.LIGHTRED_EX}Error: Find "{referencer_file_ral_path}"(not in target repo){Style.RESET_ALL} referenced {current_item.get_full_name()}')
                        continue

                    referencer_node = self.find_obj_with_lineno(referencer_file_item, referencer_pos[1])

                    # Skip name-duplicate references
                    if referencer_node.obj_name == current_item.obj_name:
                        logger.info(f"Jedi find {current_item.get_full_name()} with name_duplicate_reference, skipped")
                        continue

                    # If no ancestor relationship, record the reference
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
                task_id = task_manager.add_task(dependency_task_id=item_deps, extra=target_item)
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
    def from_project_hierarchy_json(project_hierarchy_json) -> MetaInfo:
        """
        将 project_hierarchy.json 转换到我们的数据结构
        """
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

            # Build directory chain if needed
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

            # Build parent-child relationships (O(n^2) approach)
            for item in obj_item_list:
                potential_father: Optional[DocItem] = None

                def code_contain(child_item: DocItem, parent_candidate: DocItem) -> bool:
                    if parent_candidate.code_end_line == child_item.code_end_line \
                       and parent_candidate.code_start_line == child_item.code_start_line:
                        return False
                    if parent_candidate.code_end_line < child_item.code_end_line \
                       or parent_candidate.code_start_line > child_item.code_start_line:
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
                    # Rename duplicates
                    name_id = 0
                    while (child_name + f"_{name_id}") in potential_father.children:
                        name_id += 1
                    logger.warning(
                        f"Name duplicate in {file_item.get_full_name()}: rename to {item.obj_name}->{child_name}_{name_id}"
                    )
                    child_name += f"_{name_id}"

                potential_father.children[child_name] = item

            # Convert "ClassDef"/"FunctionDef" to the correct DocItemType
            def change_items(node: DocItem):
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

        # Build tree paths and calculate depths
        target_meta_info.target_repo_hierarchical_tree.parse_tree_path(now_path=[])
        target_meta_info.target_repo_hierarchical_tree.check_depth()
        return target_meta_info


if __name__ == "__main__":
    repo_path = "some_repo_path"
    meta = MetaInfo.from_project_hierarchy_json(repo_path)
    meta.target_repo_hierarchical_tree.print_recursive()
    topology_list = meta.get_topology()
