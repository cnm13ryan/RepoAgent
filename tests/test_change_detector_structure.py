import os
import unittest
import ast

from git import Repo

from repo_agent.change_detector import ChangeDetector
from repo_agent.file_handler import FileHandler


def parse_structures_with_parent(code, file_handler):
    tree = ast.parse(code)
    file_handler.add_parent_references(tree)
    structures = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start_line = node.lineno
            end_line = file_handler.get_end_lineno(node)
            parent = None
            if hasattr(node, "parent") and isinstance(
                node.parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                parent = node.parent.name
            structures.append((type(node).__name__, node.name, start_line, end_line, parent))
    return structures


class TestIdentifyChangesInStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.test_repo_path = os.path.join(os.path.dirname(__file__), "repo_struct")
        os.makedirs(cls.test_repo_path, exist_ok=True)
        cls.repo = Repo.init(cls.test_repo_path)
        cls.repo.git.config("user.email", "ci@example.com")
        cls.repo.git.config("user.name", "CI User")

        cls.file_path = "sample.py"
        initial_code = (
            "class MyClass:\n"
            "    def method_a(self):\n"
            "        print('a')\n\n"
            "def standalone():\n"
            "    print('s')\n"
        )
        with open(os.path.join(cls.test_repo_path, cls.file_path), "w") as f:
            f.write(initial_code)
        cls.repo.git.add(A=True)
        cls.repo.git.commit("-m", "initial")

    @classmethod
    def tearDownClass(cls):
        cls.repo.close()
        os.system("rm -rf " + cls.test_repo_path)

    def test_identify_changes(self):
        modified_code = (
            "class MyClass:\n"
            "    def method_a(self):\n"
            "        print('a changed')\n\n"
            "    def method_b(self):\n"
            "        print('b')\n\n"
            "def standalone():\n"
            "    print('s changed')\n"
        )
        with open(os.path.join(self.test_repo_path, self.file_path), "w") as f:
            f.write(modified_code)
        self.repo.git.add(self.file_path)

        cd = ChangeDetector(self.test_repo_path)
        diffs = cd.get_file_diff(self.file_path, False)
        changed_lines = cd.parse_diffs(diffs)

        fh = FileHandler(self.test_repo_path, self.file_path)
        structures = parse_structures_with_parent(fh.read_file(), fh)

        result = cd.identify_changes_in_structure(changed_lines, structures)

        self.assertIn(("method_b", "MyClass"), result["added"])
        self.assertIn(("method_a", "MyClass"), result["added"])
        self.assertIn(("method_a", "MyClass"), result["removed"])
        self.assertIn(("standalone", None), result["added"])
        self.assertIn(("standalone", None), result["removed"])


if __name__ == "__main__":
    unittest.main()
