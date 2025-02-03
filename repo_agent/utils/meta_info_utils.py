import os
import itertools

import git
from colorama import Fore, Style

from repo_agent.log import logger
from repo_agent.settings import SettingsManager

latest_version_substring = "_latest_version.py"  # Suffix used for the backup (latest) version


def make_fake_files():
    """
    Analyze the current Git status (git status) and perform the following actions on any .py file
    that is modified or deleted but not staged:

      1. If it is an untracked .py file: ignore (do not parse or generate documentation).
      2. If it is a modified or deleted .py file (not yet staged):
         - Rename the original file to (..._latest_version.py).
         - Write the file content from the Git diff to the original filename.
    
    Note: If a file ends with latest_version_substring, this script will exit with an error,
          since we never want to process files already named ..._latest_version.py.
    """
    delete_fake_files()
    setting = SettingsManager.get_setting()

    repo = git.Repo(setting.project.target_repo)
    unstaged_changes = repo.index.diff(None)  # Files modified but not committed (shown in git status)
    untracked_files = repo.untracked_files    # Files present on the filesystem but not tracked by Git

    skipped_files = []  # Files to skip (won't parse, generate docs for, or calculate references)
    for file_name in untracked_files:
        if file_name.endswith(".py"):
            print(f"{Fore.LIGHTMAGENTA_EX}[SKIP untracked files]: {Style.RESET_ALL}{file_name}")
            skipped_files.append(file_name)

    # For newly added files (not yet staged) that are .py, skip them
    for diff_file in unstaged_changes.iter_change_type("A"):
        if diff_file.a_path.endswith(latest_version_substring):
            logger.error(
                "FAKE_FILE_IN_GIT_STATUS detected! Please run `delete_fake_files` and regenerate documents."
            )
            exit()
        skipped_files.append(diff_file.a_path)

    file_path_reflections = {}

    # For modified (M) or deleted (D) files (not staged)
    for diff_file in itertools.chain(
        unstaged_changes.iter_change_type("M"),
        unstaged_changes.iter_change_type("D")
    ):
        if diff_file.a_path.endswith(latest_version_substring):
            logger.error(
                "FAKE_FILE_IN_GIT_STATUS detected! Please run `delete_fake_files` and regenerate documents."
            )
            exit()

        now_file_path = diff_file.a_path  # Relative path within the repository
        if now_file_path.endswith(".py"):
            raw_file_content = diff_file.a_blob.data_stream.read().decode("utf-8")
            latest_file_path = now_file_path[:-3] + latest_version_substring

            # If the file still exists in the filesystem
            if os.path.exists(os.path.join(setting.project.target_repo, now_file_path)):
                os.rename(
                    os.path.join(setting.project.target_repo, now_file_path),
                    os.path.join(setting.project.target_repo, latest_file_path),
                )
                print(
                    f"{Fore.LIGHTMAGENTA_EX}[Save Latest Version of Code]: "
                    f"{Style.RESET_ALL}{now_file_path} -> {latest_file_path}"
                )
            else:
                # If the file was deleted but not staged
                print(
                    f"{Fore.LIGHTMAGENTA_EX}[Create Temp-File for Deleted (Not Staged) Files]: "
                    f"{Style.RESET_ALL}{now_file_path} -> {latest_file_path}"
                )
                with open(os.path.join(setting.project.target_repo, latest_file_path), "w") as writer:
                    pass  # Create an empty file

            # Overwrite the original path with the content from the diff
            with open(os.path.join(setting.project.target_repo, now_file_path), "w") as writer:
                writer.write(raw_file_content)

            file_path_reflections[now_file_path] = latest_file_path  # Original file path points to the backup path

    return file_path_reflections, skipped_files


def delete_fake_files():
    """
    After a task completes, remove all files that end with _latest_version.py. 
    If the backup file has content, rename it back to the original .py file. 
    If the backup file is empty, simply delete it and the original file.
    """
    setting = SettingsManager.get_setting()

    def delete_fake_files_recursively(filepath):
        # Recursively traverse 'filepath' and process any file ending with latest_version_substring
        files = os.listdir(filepath)
        for fi in files:
            fi_d = os.path.join(filepath, fi)
            if os.path.isdir(fi_d):
                delete_fake_files_recursively(fi_d)
            elif fi_d.endswith(latest_version_substring):
                origin_name = fi_d.replace(latest_version_substring, ".py")
                os.remove(origin_name)  # Remove the original .py file
                if os.path.getsize(fi_d) == 0:
                    print(
                        f"{Fore.LIGHTRED_EX}[Deleting Temp File]: "
                        f"{Style.RESET_ALL}{fi_d[len(str(setting.project.target_repo)):]}, "
                        f"{origin_name[len(str(setting.project.target_repo)):]}"
                    )  # type: ignore
                    os.remove(fi_d)
                else:
                    print(
                        f"{Fore.LIGHTRED_EX}[Recovering Latest Version]: "
                        f"{Style.RESET_ALL}{origin_name[len(str(setting.project.target_repo)):]}"
                        f" <- {fi_d[len(str(setting.project.target_repo)):]}"
                    )  # type: ignore
                    os.rename(fi_d, origin_name)

    delete_fake_files_recursively(setting.project.target_repo)
