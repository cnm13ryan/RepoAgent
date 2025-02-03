import os
from collections import defaultdict

import jedi


class ProjectManager:
    def __init__(self, repo_path, project_hierarchy):
        self.repo_path = repo_path
        self.project = jedi.Project(self.repo_path)
        self.project_hierarchy = os.path.join(
            self.repo_path,
            project_hierarchy,
            "project_hierarchy.json"
        )

    def get_project_structure(self):
        """
        Returns the structure of the project by recursively walking through the directory tree.

        Returns:
            str: The project structure as a string.
        """

        structure = []

        def walk_dir(root, prefix=""):
            structure.append(prefix + os.path.basename(root))
            new_prefix = prefix + "  "
            for name in sorted(os.listdir(root)):
                if name.startswith("."):  # Ignore hidden files and directories
                    continue
                path = os.path.join(root, name)
                if os.path.isdir(path):
                    walk_dir(path, new_prefix)
                elif os.path.isfile(path) and name.endswith(".py"):
                    structure.append(new_prefix + name)

        walk_dir(self.repo_path)
        return "\n".join(structure)

    def build_path_tree(self, who_reference_me, reference_who, doc_item_path):
        """
        Constructs a nested path tree based on the provided reference lists and a document item path.

        Args:
            who_reference_me (list): List of file paths referencing me.
            reference_who (list): List of file paths referenced by me.
            doc_item_path (str): The path of the document item.

        Returns:
            str: A string representation of the constructed path tree.
        """

        def tree():
            return defaultdict(tree)

        path_tree = tree()

        # Build the trees for who_reference_me and reference_who
        for path_list in [who_reference_me, reference_who]:
            for path in path_list:
                parts = path.split(os.sep)
                node = path_tree
                for part in parts:
                    node = node[part]

        # Prepend star to the last object
        parts = doc_item_path.split(os.sep)
        parts[-1] = "✳️" + parts[-1]
        node = path_tree
        for part in parts:
            node = node[part]

        def tree_to_string(tree, indent=0):
            result_str = ""
            for key, value in sorted(tree.items()):
                result_str += "    " * indent + key + "\n"
                if isinstance(value, dict):
                    result_str += tree_to_string(value, indent + 1)
            return result_str

        return tree_to_string(path_tree)


if __name__ == "__main__":
    project_manager = ProjectManager(repo_path="", project_hierarchy="")
    print(project_manager.get_project_structure())
