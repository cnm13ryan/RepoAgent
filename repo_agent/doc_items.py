"""Data structures for repository documentation.

This module defines lightweight containers used during
repository scanning and documentation generation. It has no
knowledge of how trees are built or how references are
resolved; those behaviours live in dedicated modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto, unique
from typing import Any, Dict, List, Optional

from colorama import Fore, Style


@unique
class DocItemType(Enum):
    """Granular types of documented objects."""

    _repo = auto()
    _dir = auto()
    _file = auto()
    _class = auto()
    _class_function = auto()
    _function = auto()
    _sub_function = auto()
    _global_var = auto()

    def to_str(self) -> str:
        """Return the parser name for this type."""
        if self == DocItemType._class:
            return "ClassDef"
        if self in {
            DocItemType._function,
            DocItemType._class_function,
            DocItemType._sub_function,
        }:
            return "FunctionDef"
        return self.name

    def print_self(self) -> str:
        """Return a colorised name used when printing trees."""
        color = {
            DocItemType._dir: Fore.GREEN,
            DocItemType._file: Fore.YELLOW,
            DocItemType._class: Fore.RED,
        }.get(self, Fore.WHITE)
        if self in {
            DocItemType._function,
            DocItemType._sub_function,
            DocItemType._class_function,
        }:
            color = Fore.BLUE
        return color + self.name + Style.RESET_ALL


@unique
class DocItemStatus(Enum):
    """State of documentation for a node."""

    doc_up_to_date = auto()
    doc_has_not_been_generated = auto()
    code_changed = auto()
    add_new_referencer = auto()
    referencer_not_exist = auto()


@dataclass(eq=False)
class DocItem:
    """Node in the project hierarchy."""

    item_type: DocItemType = DocItemType._class_function
    item_status: DocItemStatus = DocItemStatus.doc_has_not_been_generated

    obj_name: str = ""
    code_start_line: int = -1
    code_end_line: int = -1
    md_content: List[str] = field(default_factory=list)
    content: Dict[Any, Any] = field(default_factory=dict)

    children: Dict[str, "DocItem"] = field(default_factory=dict, compare=False)
    father: Optional["DocItem"] = field(default=None, compare=False)

    depth: int = 0
    tree_path: List["DocItem"] = field(default_factory=list, compare=False)
    max_reference_ansce: Optional["DocItem"] = field(default=None, compare=False)

    reference_who: List["DocItem"] = field(default_factory=list, compare=False)
    who_reference_me: List["DocItem"] = field(default_factory=list, compare=False)
    special_reference_type: List[bool] = field(default_factory=list)

    reference_who_name_list: List[str] = field(default_factory=list)
    who_reference_me_name_list: List[str] = field(default_factory=list)

    has_task: bool = False
    multithread_task_id: int = -1

    @staticmethod
    def has_ans_relation(a: "DocItem", b: "DocItem") -> Optional["DocItem"]:
        """Return the ancestor node if ``a`` and ``b`` are related."""
        if b in a.tree_path:
            return b
        if a in b.tree_path:
            return a
        return None

    def get_travel_list(self) -> List["DocItem"]:
        """Return ``self`` and all descendants."""
        nodes = [self]
        for child in self.children.values():
            nodes.extend(child.get_travel_list())
        return nodes

    def check_depth(self) -> int:
        """Compute depth of ``self`` recursively."""
        if not self.children:
            self.depth = 0
            return 0
        max_child_depth = 0
        for child in self.children.values():
            child_depth = child.check_depth()
            max_child_depth = max(child_depth, max_child_depth)
        self.depth = max_child_depth + 1
        return self.depth

    def parse_tree_path(self, now_path: List["DocItem"]) -> None:
        """Populate ``tree_path`` for ``self`` and all children."""
        self.tree_path = now_path + [self]
        for child in self.children.values():
            child.parse_tree_path(self.tree_path)

    def get_file_name(self) -> str:
        return self.get_full_name().split(".py")[0] + ".py"

    def get_full_name(self, strict: bool = False) -> str:
        """Return ``/`` separated path from repo root to this item."""
        if self.father is None:
            return self.obj_name
        name_list: List[str] = []
        current: Optional[DocItem] = self
        while current is not None:
            current_name = current.obj_name
            if strict and current.father is not None:
                for name, item in current.father.children.items():
                    if item == current:
                        current_name = name
                        break
                if current_name != current.obj_name:
                    current_name += "(name_duplicate_version)"
            name_list.insert(0, current_name)
            current = current.father
        name_list = name_list[1:]
        return "/".join(name_list)

    def find(self, recursive_file_path: List[str]) -> Optional["DocItem"]:
        """Find a descendant following ``recursive_file_path`` from the repo root."""
        assert self.item_type == DocItemType._repo
        pos = 0
        current_item: DocItem = self
        while pos < len(recursive_file_path):
            if recursive_file_path[pos] not in current_item.children:
                return None
            current_item = current_item.children[recursive_file_path[pos]]
            pos += 1
        return current_item

    @staticmethod
    def check_has_task(current_item: "DocItem", ignore_list: List[str] = None) -> None:
        """Mark nodes that need doc generation."""
        ignore_list = ignore_list or []
        if need_to_generate(current_item, ignore_list=ignore_list):
            current_item.has_task = True
        for child in current_item.children.values():
            DocItem.check_has_task(child, ignore_list)
            current_item.has_task = current_item.has_task or child.has_task

    def print_recursive(
        self, *, indent: int = 0, print_content: bool = False,
        diff_status: bool = False, ignore_list: List[str] | None = None
    ) -> None:
        """Pretty print the tree beneath this node."""
        ignore_list = ignore_list or []

        def _indent(i: int) -> str:
            return "" if i == 0 else "  " * i + "|-"

        item_name = self.obj_name
        from repo_agent.settings import SettingsManager

        if self.item_type == DocItemType._repo:
            item_name = SettingsManager.get_setting().project.target_repo

        if diff_status and need_to_generate(self, ignore_list=ignore_list):
            print(f"{_indent(indent)}{self.item_type.print_self()}: {item_name} : {self.item_status.name}")
        else:
            print(f"{_indent(indent)}{self.item_type.print_self()}: {item_name}")
        for child in self.children.values():
            if diff_status and not child.has_task:
                continue
            child.print_recursive(
                indent=indent + 1,
                print_content=print_content,
                diff_status=diff_status,
                ignore_list=ignore_list,
            )


def need_to_generate(doc_item: DocItem, ignore_list: List[str] | None = None) -> bool:
    """Return ``True`` if ``doc_item`` requires documentation generation."""
    ignore_list = ignore_list or []
    if doc_item.item_status == DocItemStatus.doc_up_to_date:
        return False
    rel_file_path = doc_item.get_full_name()
    if doc_item.item_type in {DocItemType._file, DocItemType._dir, DocItemType._repo}:
        return False
    parent = doc_item.father
    while parent:
        if parent.item_type == DocItemType._file:
            if any(rel_file_path.startswith(ignore_item) for ignore_item in ignore_list):
                return False
            return True
        parent = parent.father
    return False
