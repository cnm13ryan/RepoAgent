import json
import os
import shutil
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from colorama import Fore, Style
from tqdm import tqdm

from repo_agent.change_detector import ChangeDetector
from repo_agent.chat_engine import ChatEngine
from repo_agent.doc_meta_info import DocItem, DocItemStatus, MetaInfo, need_to_generate
from repo_agent.file_handler import FileHandler
from repo_agent.log import logger
from repo_agent.multi_task_dispatch import worker
from repo_agent.project_manager import ProjectManager
from repo_agent.settings import SettingsManager
from repo_agent.utils.meta_info_utils import delete_fake_files, make_fake_files


class Runner:
    def __init__(self):
        """
        Initialize the Runner with settings, the project manager, and
        the meta_info structure that tracks documentation state.
        """
        self.setting = SettingsManager.get_setting()
        self.absolute_project_hierarchy_path = (
            self.setting.project.target_repo / self.setting.project.hierarchy_name
        )

        # Core helpers
        self.project_manager = ProjectManager(
            repo_path=self.setting.project.target_repo,
            project_hierarchy=self.setting.project.hierarchy_name,
        )
        self.change_detector = ChangeDetector(
            repo_path=self.setting.project.target_repo
        )
        self.chat_engine = ChatEngine(project_manager=self.project_manager)

        # Load or create meta_info
        if self.absolute_project_hierarchy_path.exists():
            self.meta_info = MetaInfo.from_checkpoint_path(self.absolute_project_hierarchy_path)
        else:
            file_path_reflections, jump_files = make_fake_files()
            self.meta_info = MetaInfo.init_meta_info(file_path_reflections, jump_files)

        # Always checkpoint after loading or creating meta_info
        self.meta_info.checkpoint(target_dir_path=self.absolute_project_hierarchy_path)

        self.runner_lock = threading.Lock()


    def run(self):
        """
        Main entry point: updates or generates documentation based on
        whether a documented version exists. Includes incremental changes
        or first-time generation paths.
        """
        # Guard clause: if brand-new doc state, do a first-time generation
        if self.meta_info.document_version == "":
            self.first_generate()
            self.meta_info.checkpoint(
                target_dir_path=self.absolute_project_hierarchy_path,
                flash_reference_relation=True,
            )
            return

        # If not in generation process, detect changes and set up tasks
        if not self.meta_info.in_generation_process:
            logger.info("Starting to detect changes.")

            file_path_reflections, jump_files = make_fake_files()
            new_meta_info = MetaInfo.init_meta_info(file_path_reflections, jump_files)
            new_meta_info.load_doc_from_older_meta(self.meta_info)

            self.meta_info = new_meta_info
            self.meta_info.in_generation_process = True

        # Build the task_manager from meta_info
        check_task_available_func = partial(
            need_to_generate, ignore_list=self.setting.project.ignore_list
        )
        task_manager = self.meta_info.get_task_manager(
            self.meta_info.target_repo_hierarchical_tree,
            task_available_func=check_task_available_func,
        )

        # Log deleted items detected
        for item_name, item_type in self.meta_info.deleted_items_from_older_meta:
            print(
                f"{Fore.LIGHTMAGENTA_EX}[Dir/File/Obj Delete Detected]: {Style.RESET_ALL} {item_type} {item_name}"
            )

        # Print tasks that need doc generation
        self.meta_info.print_task_list(task_manager.task_dict)

        if task_manager.all_success:
            logger.info("No tasks in the queue, all documents are completed and up to date.")

        # Spawn worker threads
        threads = [
            threading.Thread(
                target=worker,
                args=(task_manager, process_id, self.generate_doc_for_a_single_item),
            )
            for process_id in range(self.setting.project.max_thread_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        # Wrap up once tasks are done
        self.meta_info.in_generation_process = False
        self.meta_info.document_version = self.change_detector.repo.head.commit.hexsha

        self.meta_info.checkpoint(
            target_dir_path=self.absolute_project_hierarchy_path,
            flash_reference_relation=True,
        )
        logger.info("Doc has been forwarded to the latest version.")

        self.markdown_refresh()
        delete_fake_files()

        logger.info("Starting to git-add DocMetaInfo and newly generated Docs")
        time.sleep(1)

        # Stage new or changed doc files
        git_add_result = self.change_detector.add_unstaged_files()
        if git_add_result:
            logger.info(f"Added {[file for file in git_add_result]} to the staging area.")

        # Optionally: self.git_commit("Your commit message")


    def first_generate(self):
        """
        Perform a one-time generation pass for all documentation,
        then refresh and store the resulting doc info.
        """
        logger.info("Starting to generate documentation")

        check_task_available_func = partial(
            need_to_generate, ignore_list=self.setting.project.ignore_list
        )
        task_manager = self.meta_info.get_topology(check_task_available_func)
        before_task_len = len(task_manager.task_dict)

        # Either initialize a new task list or continue an existing one
        if not self.meta_info.in_generation_process:
            self.meta_info.in_generation_process = True
            logger.info("Init a new task-list")
        else:
            logger.info("Load from an existing task-list")

        self.meta_info.print_task_list(task_manager.task_dict)

        try:
            # Spawn threads for doc generation
            threads = [
                threading.Thread(
                    target=worker,
                    args=(task_manager, process_id, self.generate_doc_for_a_single_item),
                )
                for process_id in range(self.setting.project.max_thread_count)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            # Refresh markdown
            self.markdown_refresh()

            # Set doc version and checkpoint
            self.meta_info.document_version = self.change_detector.repo.head.commit.hexsha
            self.meta_info.in_generation_process = False

            self.meta_info.checkpoint(
                target_dir_path=self.absolute_project_hierarchy_path
            )

            logger.info(
                f"Successfully generated {before_task_len - len(task_manager.task_dict)} documents."
            )

        except BaseException as e:
            logger.error(
                f"An error occurred: {e}. "
                f"{before_task_len - len(task_manager.task_dict)} docs are generated at this time"
            )


    def generate_doc_for_a_single_item(self, doc_item: DocItem):
        """
        Generate or update the documentation for a single item (function, class, etc.)
        using ChatEngine.
        """
        try:
            # Flattened guard clause for ignoring or skipping generation
            if not need_to_generate(doc_item, self.setting.project.ignore_list):
                print(
                    f"Content ignored/Document generated, skipping: {doc_item.get_full_name()}"
                )
                return

            # Otherwise, proceed with doc generation
            print(
                f" -- Generating document  "
                f"{Fore.LIGHTYELLOW_EX}{doc_item.item_type.name}: {doc_item.get_full_name()}"
                f"{Style.RESET_ALL}"
            )
            response_message = self.chat_engine.generate_doc(doc_item=doc_item)
            doc_item.md_content.append(response_message)  # type: ignore
            doc_item.item_status = DocItemStatus.doc_up_to_date

            self.meta_info.checkpoint(
                target_dir_path=self.absolute_project_hierarchy_path
            )

        except Exception:
            logger.exception(
                f"Document generation failed after multiple attempts, skipping: {doc_item.get_full_name()}"
            )
            doc_item.item_status = DocItemStatus.doc_has_not_been_generated


    def markdown_refresh(self):
        """
        Rebuild the entire Markdown folder from in-memory doc items.
        Deletes the old folder, recreates it, and writes out .md files for each file's children.
        """
        with self.runner_lock:
            markdown_folder = (
                Path(self.setting.project.target_repo)
                / self.setting.project.markdown_docs_name
            )

            # Delete old docs if present
            if markdown_folder.exists():
                logger.debug(f"Deleting existing contents of {markdown_folder}")
                shutil.rmtree(markdown_folder)

            # Recreate folder
            markdown_folder.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Created markdown folder at {markdown_folder}")

        file_item_list = self.meta_info.get_all_files()
        logger.debug(f"Found {len(file_item_list)} files to process.")

        for file_item in tqdm(file_item_list):
            # Helper to see if this item has any doc content
            def recursive_check(doc_item) -> bool:
                if doc_item.md_content:
                    return True
                for child in doc_item.children.values():
                    if recursive_check(child):
                        return True
                return False

            if not recursive_check(file_item):
                logger.debug(
                    f"No documentation content for: {file_item.get_full_name()}, skipping."
                )
                continue

            # Build markdown by recursing children
            markdown = ""
            for child in file_item.children.values():
                markdown += self.to_markdown(child, 2)

            if not markdown:
                logger.warning(
                    f"No markdown content generated for: {file_item.get_full_name()}"
                )
                continue

            # Determine output path
            file_path = Path(self.setting.project.markdown_docs_name) / file_item.get_file_name().replace(".py", ".md")
            abs_file_path = self.setting.project.target_repo / file_path
            logger.debug(f"Writing markdown to: {abs_file_path}")

            abs_file_path.parent.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Ensured directory exists: {abs_file_path.parent}")

            # Attempt file writing, with lock and retries
            with self.runner_lock:
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
            f"Markdown documents have been refreshed at {self.setting.project.markdown_docs_name}"
        )


    def to_markdown(self, item, now_level: int) -> str:
        """
        Recursively convert a doc item into Markdown content,
        including heading levels, parameters, and child items.
        """
        markdown_content = "#" * now_level + f" {item.item_type.to_str()} {item.obj_name}"

        if "params" in item.content.keys() and item.content["params"]:
            markdown_content += f"({', '.join(item.content['params'])})"
        markdown_content += "\n"

        # Use the latest doc content, or placeholder text if none exists
        if item.md_content:
            markdown_content += f"{item.md_content[-1]}\n"
        else:
            markdown_content += "Doc is waiting to be generated...\n"

        # Recurse into children
        for child in item.children.values():
            markdown_content += self.to_markdown(child, now_level + 1)
            markdown_content += "***\n"

        return markdown_content


    def add_new_item(self, file_handler, json_data):
        """
        For a new file, record all discovered objects, generate docs, and
        write them to JSON and Markdown files.
        """
        file_dict = {}
        source_code = file_handler.read_file()

        # Discover objects and generate docs
        for (structure_type, name, start_line, end_line, parent, params) in file_handler.get_functions_and_classes(source_code):
            code_info = file_handler.get_obj_code_info(
                structure_type, name, start_line, end_line, parent, params
            )
            response_message = self.chat_engine.generate_doc(code_info, file_handler)
            code_info["md_content"] = response_message.content

            file_dict[name] = code_info

        json_data[file_handler.file_path] = file_dict

        # Write updated JSON
        with open(self.project_manager.project_hierarchy, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=4, ensure_ascii=False)
        logger.info(
            f"The structural information of newly added file {file_handler.file_path} "
            f"has been written into a JSON file."
        )

        # Convert to markdown
        markdown = file_handler.convert_to_markdown_file(file_handler.file_path)
        file_handler.write_file(
            os.path.join(
                self.project_manager.repo_path,
                self.setting.project.markdown_docs_name,
                file_handler.file_path.replace(".py", ".md"),
            ),
            markdown,
        )
        logger.info(f"Generated Markdown doc for new file {file_handler.file_path}.")


    def process_file_changes(self, repo_path, file_path, is_new_file):
        """
        Called in a loop for each changed file; updates or adds docs for
        changed objects in that file.
        """
        file_handler = FileHandler(repo_path=repo_path, file_path=file_path)
        source_code = file_handler.read_file()

        changed_lines = self.change_detector.parse_diffs(
            self.change_detector.get_file_diff(file_path, is_new_file)
        )
        changes_in_pyfile = self.change_detector.identify_changes_in_structure(
            changed_lines, file_handler.get_functions_and_classes(source_code)
        )
        logger.info(f"Detected changed objects:\n{changes_in_pyfile}")

        # Load JSON data
        with open(self.project_manager.project_hierarchy, "r", encoding="utf-8") as f:
            json_data = json.load(f)

        # Guard clause: if file not in JSON, it's a new item
        if file_handler.file_path not in json_data:
            self.add_new_item(file_handler, json_data)

            git_add_result = self.change_detector.add_unstaged_files()
            if git_add_result:
                logger.info(f"Added {[file for file in git_add_result]} to staging.")
            return

        # Otherwise, update existing info
        json_data[file_handler.file_path] = self.update_existing_item(
            json_data[file_handler.file_path],
            file_handler,
            changes_in_pyfile
        )

        # Write JSON
        with open(self.project_manager.project_hierarchy, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=4, ensure_ascii=False)
        logger.info(f"Updated JSON structure for {file_handler.file_path}.")

        # Convert updated structures to Markdown
        markdown = file_handler.convert_to_markdown_file(file_handler.file_path)
        file_handler.write_file(
            os.path.join(
                self.setting.project.markdown_docs_name,
                file_handler.file_path.replace(".py", ".md"),
            ),
            markdown,
        )
        logger.info(f"Updated Markdown doc for {file_handler.file_path}.")

        # Stage
        git_add_result = self.change_detector.add_unstaged_files()
        if git_add_result:
            logger.info(f"Added {[file for file in git_add_result]} to the staging area.")


    def update_existing_item(self, file_dict, file_handler, changes_in_pyfile):
        """
        Update existing file structures when objects are added/removed
        or changed. Spawns doc generation for newly added objects.
        """
        new_obj, del_obj = self.get_new_objects(file_handler)

        # Remove truly deleted objects
        for obj_name in del_obj:
            if obj_name in file_dict:
                del file_dict[obj_name]
                logger.info(f"Removed object {obj_name}.")

        # Generate new structure from code
        current_objects = file_handler.generate_file_structure(file_handler.file_path)
        current_info_dict = {obj["name"]: obj for obj in current_objects.values()}

        # Update or add new items in the file_dict
        for current_obj_name, current_obj_info in current_info_dict.items():
            if current_obj_name in file_dict:
                file_dict[current_obj_name]["type"] = current_obj_info["type"]
                file_dict[current_obj_name]["code_start_line"] = current_obj_info["code_start_line"]
                file_dict[current_obj_name]["code_end_line"] = current_obj_info["code_end_line"]
                file_dict[current_obj_name]["parent"] = current_obj_info["parent"]
                file_dict[current_obj_name]["name_column"] = current_obj_info["name_column"]
            else:
                file_dict[current_obj_name] = current_obj_info

        # Determine referencers for newly added objects
        referencer_list = []
        for obj_name, _ in changes_in_pyfile["added"]:
            for current_object in current_objects.values():
                if obj_name == current_object["name"]:
                    referencer_list.append({
                        "obj_name": obj_name,
                        "obj_referencer_list": self.project_manager.find_all_referencer(
                            variable_name=current_object["name"],
                            file_path=file_handler.file_path,
                            line_number=current_object["code_start_line"],
                            column_number=current_object["name_column"],
                        ),
                    })

        # Generate docs for newly added objects in parallel
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            for changed_obj in changes_in_pyfile["added"]:
                for ref_obj in referencer_list:
                    if changed_obj[0] == ref_obj["obj_name"]:
                        future = executor.submit(
                            self.update_object,
                            file_dict,
                            file_handler,
                            changed_obj[0],
                            ref_obj["obj_referencer_list"],
                        )
                        print(
                            f"Generating doc for {Fore.CYAN}{file_handler.file_path}"
                            f"{Style.RESET_ALL} => object {Fore.CYAN}{changed_obj[0]}{Style.RESET_ALL}."
                        )
                        futures.append(future)
            for future in futures:
                future.result()

        return file_dict


    def update_object(self, file_dict, file_handler, obj_name, obj_referencer_list):
        """
        Given a single object's metadata, update the doc content using ChatEngine.
        """
        # Flattened guard: only update if the object is present
        if obj_name not in file_dict:
            return

        obj = file_dict[obj_name]
        response_message = self.chat_engine.generate_doc(obj, file_handler, obj_referencer_list)
        obj["md_content"] = response_message.content


    def get_new_objects(self, file_handler):
        """
        Compare current and previous file versions to detect
        new or deleted objects (functions/classes).
        """
        current_version, previous_version = file_handler.get_modified_file_versions()

        parse_current_py = file_handler.get_functions_and_classes(current_version)
        parse_previous_py = (
            file_handler.get_functions_and_classes(previous_version)
            if previous_version
            else []
        )

        current_obj = {f[1] for f in parse_current_py}
        previous_obj = {f[1] for f in parse_previous_py}

        new_obj = list(current_obj - previous_obj)
        del_obj = list(previous_obj - current_obj)
        return new_obj, del_obj


    def git_commit(self, commit_message):
        """
        Simple Git commit wrapper that disables pre-commit hooks via --no-verify.
        """
        try:
            subprocess.check_call(
                ["git", "commit", "--no-verify", "-m", commit_message],
                shell=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"An error occurred while trying to commit {str(e)}")


if __name__ == "__main__":
    runner = Runner()
    runner.run()
    logger.info("文档任务完成。")
