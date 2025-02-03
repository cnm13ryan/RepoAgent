import os
import itertools

import git
from colorama import Fore, Style

from repo_agent.log import logger
from repo_agent.settings import SettingsManager

latest_version_substring = "_latest_version.py"  # stores the backup version suffix


def make_fake_files():
    """
    根据当前Git状态（git status）检测尚未提交的更改，并对每个Python文件进行如下处理：
      1. 如果是未追踪（untracked）的 .py 文件：忽略并跳过（不解析/不生成文档）。
      2. 如果是已修改或已删除但未git add的 .py 文件：将原文件重命名为 ..._latest_version.py
         然后用最新的内容（来自git的diff）写回原文件名。
    注意：任何文件名不能以 latest_version_substring 结尾，否则会退出并报错。
    """
    delete_fake_files()
    setting = SettingsManager.get_setting()

    repo = git.Repo(setting.project.target_repo)
    unstaged_changes = repo.index.diff(None)  # 在git status里，但是有修改没提交
    untracked_files = repo.untracked_files  # 在文件系统里，但没在git里的文件

    skipped_files = []  # 这里面的内容不parse、不生成文档，并且引用关系也不计算他们
    for file_name in untracked_files:
        if file_name.endswith(".py"):
            print(f"{Fore.LIGHTMAGENTA_EX}[SKIP untracked files]: {Style.RESET_ALL}{file_name}")
            skipped_files.append(file_name)

    # 新增的、没有add的文件，都不处理
    for diff_file in unstaged_changes.iter_change_type("A"):
        if diff_file.a_path.endswith(latest_version_substring):
            logger.error(
                "FAKE_FILE_IN_GIT_STATUS detected! suggest to use `delete_fake_files` and re-generate document"
            )
            exit()
        skipped_files.append(diff_file.a_path)

    file_path_reflections = {}

    # 获取修改过（M）或删除（D）的文件
    for diff_file in itertools.chain(
        unstaged_changes.iter_change_type("M"),
        unstaged_changes.iter_change_type("D")
    ):
        if diff_file.a_path.endswith(latest_version_substring):
            logger.error(
                "FAKE_FILE_IN_GIT_STATUS detected! suggest to use `delete_fake_files` and re-generate document"
            )
            exit()

        now_file_path = diff_file.a_path  # repo_path的相对路径
        if now_file_path.endswith(".py"):
            raw_file_content = diff_file.a_blob.data_stream.read().decode("utf-8")
            latest_file_path = now_file_path[:-3] + latest_version_substring

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
                # 针对已删除但未提交（staged）的文件
                print(
                    f"{Fore.LIGHTMAGENTA_EX}[Create Temp-File for Deleted(But not Staged) Files]: "
                    f"{Style.RESET_ALL}{now_file_path} -> {latest_file_path}"
                )
                with open(os.path.join(setting.project.target_repo, latest_file_path), "w") as writer:
                    pass

            with open(os.path.join(setting.project.target_repo, now_file_path), "w") as writer:
                writer.write(raw_file_content)

            file_path_reflections[now_file_path] = latest_file_path  # real指向fake

    return file_path_reflections, skipped_files


def delete_fake_files():
    """
    在任务执行完成以后，删除所有 _latest_version.py 文件并恢复/删除原文件。
    """
    setting = SettingsManager.get_setting()

    def delete_fake_files_recursively(filepath):
        # 遍历filepath下所有文件，包括子目录
        files = os.listdir(filepath)
        for fi in files:
            fi_d = os.path.join(filepath, fi)
            if os.path.isdir(fi_d):
                delete_fake_files_recursively(fi_d)
            elif fi_d.endswith(latest_version_substring):
                origin_name = fi_d.replace(latest_version_substring, ".py")
                os.remove(origin_name)
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
