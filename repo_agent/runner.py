import threading
import os, json
from file_handler import FileHandler
from change_detector import ChangeDetector
from project_manager import ProjectManager
from chat_engine import ChatEngine
from concurrent.futures import ThreadPoolExecutor, as_completed
from doc_meta_info import MetaInfo, DocItem, DocItemType, DocItemStatus
import yaml
from tqdm import tqdm
from typing import List
from functools import partial
import subprocess
from loguru import logger
import json
from config import CONFIG
from multi_task_dispatch import worker



def need_to_generate(doc_item: DocItem, ignore_list: List) -> bool:
    """只生成item的，文件及更高粒度都跳过。另外如果属于一个blacklist的文件也跳过"""
    if doc_item.item_status == DocItemStatus.doc_up_to_date:
        return False
    rel_file_path = doc_item.get_full_name()
    if doc_item.item_type in [DocItemType._file, DocItemType._dir, DocItemType._repo]: #暂时不生成file及以上的doc
        return False
    doc_item = doc_item.father
    while doc_item:
        if doc_item.item_type == DocItemType._file:
            # 如果当前文件在忽略列表中，或者在忽略列表某个文件路径下，则跳过
            if any(rel_file_path.startswith(ignore_item) for ignore_item in ignore_list):
                return False
            else:
                return True
        doc_item = doc_item.father
    return False

def load_whitelist():
    if CONFIG["whitelist_path"] != None:
        assert os.path.exists(CONFIG["whitelist_path"]), f"whitelist_path must be a json-file,and must exists: {CONFIG['whitelist_path']}"
        with open(CONFIG["whitelist_path"], "r") as reader:
            white_list_json_data = json.load(reader)
        # for i in range(len(white_list_json_data)):
        #     white_list_json_data[i]["file_path"] = white_list_json_data[i]["file_path"].replace("https://github.com/huggingface/transformers/blob/v4.36.1/","")
        return white_list_json_data
    else:
        return None

class Runner:
    def __init__(self):
        self.project_manager = ProjectManager(repo_path=CONFIG['repo_path'],project_hierarchy=CONFIG['project_hierarchy']) 
        self.change_detector = ChangeDetector(repo_path=CONFIG['repo_path'])
        self.chat_engine = ChatEngine(CONFIG=CONFIG)

        if not os.path.exists(os.path.join(CONFIG['repo_path'], CONFIG['project_hierarchy'])):
            self.meta_info = MetaInfo.init_from_project_path(CONFIG['repo_path'])
            self.meta_info.checkpoint(target_dir_path=os.path.join(CONFIG['repo_path'], CONFIG['project_hierarchy']))
        else:
            self.meta_info = MetaInfo.from_checkpoint_path(os.path.join(CONFIG['repo_path'], CONFIG['project_hierarchy']))
        self.meta_info.white_list = load_whitelist()
        self.meta_info.checkpoint(target_dir_path=os.path.join(CONFIG['repo_path'],CONFIG['project_hierarchy']))
        self.runner_lock = threading.Lock()

    def generate_doc_for_a_single_item(self, doc_item: DocItem):
        """为一个对象生成文档
        """
        try:
            rel_file_path = doc_item.get_full_name()

            ignore_list = CONFIG.get('ignore_list', [])
            if not need_to_generate(doc_item, ignore_list):
                logger.info(f"内容被忽略/文档已生成，跳过：{doc_item.get_full_name()}")
            else:
                logger.info(f" -- 正在生成{doc_item.get_full_name()} 对象文档...")
                file_handler = FileHandler(CONFIG['repo_path'], rel_file_path)
                response_message = self.chat_engine.generate_doc(
                    doc_item = doc_item,
                    file_handler = file_handler,
                )
                doc_item.md_content.append(response_message.content)
                doc_item.item_status = DocItemStatus.doc_up_to_date
                self.meta_info.checkpoint(target_dir_path=os.path.join(CONFIG['repo_path'],CONFIG['project_hierarchy']))
        except Exception as e:
            logger.info(f" 多次尝试后生成文档失败，跳过：{doc_item.get_full_name()}")
            logger.info("Error:", e)
            doc_item.item_status = DocItemStatus.doc_has_not_been_generated

        

    def first_generate(self):
        """
        生成所有文档,
        如果生成结束，self.meta_info.document_version会变成0(之前是-1)
        每生成一个obj的doc，会实时同步回文件系统里。如果中间报错了，下次会自动load，按照文件顺序接着生成。
        **注意**：这个生成first_generate的过程中，目标仓库代码不能修改。也就是说，一个document的生成过程必须绑定代码为一个版本。
        """
        logger.info("Starting to generate documentation")
        ignore_list = CONFIG.get('ignore_list', [])
        check_task_available_func = partial(need_to_generate, ignore_list=ignore_list)
        task_manager = self.meta_info.get_topology(check_task_available_func) #将按照此顺序生成文档
        # topology_list = [item for item in topology_list if need_to_generate(item, ignore_list)]
        before_task_len = len(task_manager.task_dict)

        if not self.meta_info.in_generation_process:
            self.meta_info.in_generation_process = True
            logger.info("Init a new task-list")
        else:
            logger.info("Load from an existing task-list")
        # self.meta_info.print_task_list(task_manager.task_dict)      

        try:
            task_manager.sync_func = self.markdown_refresh
            threads = [threading.Thread(target=worker, args=(task_manager,process_id, self.generate_doc_for_a_single_item)) for process_id in range(CONFIG["max_thread_count"])]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.meta_info.document_version = self.change_detector.repo.head.commit.hexsha
            self.meta_info.in_generation_process = False
            self.meta_info.checkpoint(target_dir_path=os.path.join(CONFIG['repo_path'],CONFIG['project_hierarchy']))
            logger.info(f"成功生成了 {before_task_len - len(task_manager.task_dict)} 个文档")

        except BaseException as e:
            logger.info(f"Finding an error as {e}, {before_task_len - len(task_manager.task_dict)} docs are generated at this time")

    def markdown_refresh(self):
        """将目前最新的document信息写入到一个markdown格式的文件夹里(不管markdown内容是不是变化了)
        """
        with self.runner_lock:
            file_item_list = self.meta_info.get_all_files()
            for file_item in tqdm(file_item_list):
                def recursive_check(doc_item: DocItem) -> bool: #检查一个file内是否存在doc
                    if doc_item.md_content != []:
                        return True
                    for _,child in doc_item.children.items():
                        if recursive_check(child):
                            return True
                    return False
                if recursive_check(file_item) == False:
                    # logger.info(f"不存在文档内容，跳过：{file_item.get_full_name()}")
                    continue
                rel_file_path = file_item.get_full_name()
                # file_handler = FileHandler(CONFIG['repo_path'], rel_file_path)
                def to_markdown(item: DocItem, now_level: int) -> str:
                    markdown_content = ""
                    markdown_content += "#"*now_level + f" {item.item_type.name} {item.obj_name}"
                    if "params" in item.content.keys() and len(item.content["params"]) > 0:
                        markdown_content += f"({', '.join(item.content['params'])})"
                    markdown_content += "\n"
                    markdown_content += f"{item.md_content[-1] if len(item.md_content) >0 else 'Doc has not been generated...'}\n"
                    for _, child in item.children.items():
                        markdown_content += to_markdown(child, now_level+1)
                    return markdown_content
                    
                markdown = ""
                for _, child in file_item.children.items():
                    markdown += to_markdown(child, 2)
                assert markdown != None, f"markdown内容为空，文件路径为{rel_file_path}"
                # 写入markdown内容到.md文件
                file_path = os.path.join(CONFIG['Markdown_Docs_folder'], file_item.get_file_name().replace('.py', '.md'))
                if file_path.startswith('/'):
                    # 移除开头的 '/'
                    file_path = file_path[1:]
                abs_file_path = os.path.join(CONFIG["repo_path"], file_path)
                os.makedirs(os.path.dirname(abs_file_path), exist_ok=True)
                with open(abs_file_path, 'w', encoding='utf-8') as file:
                    file.write(markdown)

            logger.info(f"markdown document has been refreshed at {CONFIG['Markdown_Docs_folder']}")

    def git_commit(self, commit_message):
        try:
            subprocess.check_call(['git', 'commit', '--no-verify', '-m', commit_message])
        except subprocess.CalledProcessError as e:
            print(f'An error occurred while trying to commit {str(e)}')


    def run(self):
        """
        Runs the document update process.

        This method detects the changed Python files, processes each file, and updates the documents accordingly.

        Returns:
            None
        """

        if self.meta_info.document_version == "": 
            # 根据document version自动检测是否仍在最初生成的process里
            self.first_generate()
            self.meta_info.checkpoint(target_dir_path=os.path.join(CONFIG['repo_path'], CONFIG['project_hierarchy']), flash_reference_relation=True)
            return

        if not self.meta_info.in_generation_process:
            logger.info("Starting to detect changes.")

            """采用新的办法
            1.新建一个project-hierachy
            2.和老的hierarchy做merge,处理以下情况：
            - 创建一个新文件：需要生成对应的doc
            - 文件、对象被删除：对应的doc也删除(按照目前的实现，文件重命名算是删除再添加)
            - 引用关系变了：对应的obj-doc需要重新生成
            
            merge后的new_meta_info中：
            1.新建的文件没有文档，因此metainfo merge后还是没有文档
            2.被删除的文件和obj，本来就不在新的meta里面，相当于文档被自动删除了
            3.只需要观察被修改的文件，以及引用关系需要被通知的文件去重新生成文档"""
            new_meta_info = MetaInfo.init_from_project_path(CONFIG["repo_path"])
            new_meta_info.load_doc_from_older_meta(self.meta_info)

            self.meta_info = new_meta_info
            self.meta_info.in_generation_process = True

        # 处理任务队列
        ignore_list = CONFIG.get('ignore_list', [])
        check_task_available_func = partial(need_to_generate, ignore_list=ignore_list)

        task_manager = self.meta_info.get_task_manager(self.meta_info.target_repo_hierarchical_tree,task_available_func=check_task_available_func)
        self.meta_info.print_task_list(task_manager.task_dict)

        task_manager.sync_func = self.markdown_refresh
        threads = [threading.Thread(target=worker, args=(task_manager,process_id, self.generate_doc_for_a_single_item)) for process_id in range(CONFIG["max_thread_count"])]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.meta_info.in_generation_process = False
        self.meta_info.document_version = self.change_detector.repo.head.commit.hexsha

        self.meta_info.checkpoint(target_dir_path=os.path.join(CONFIG['repo_path'],CONFIG['project_hierarchy']), flash_reference_relation=True)
        logger.info(f"Doc has been forwarded to the latest version")

        self.markdown_refresh()
        


    def process_file_changes(self, repo_path, file_path, is_new_file):
        """
        This function is called in the loop of detected changed files. Its purpose is to process changed files according to the absolute file path, including new files and existing files.
        Among them, changes_in_pyfile is a dictionary that contains information about the changed structures. An example format is: {'added': {'add_context_stack', '__init__'}, 'removed': set()}

        Args:
            repo_path (str): The path to the repository.
            file_path (str): The relative path to the file.
            is_new_file (bool): Indicates whether the file is new or not.

        Returns:
            None
        """
        file_handler = FileHandler(repo_path=repo_path, file_path=file_path) # 变更文件的操作器
        # 获取整个py文件的代码
        source_code = file_handler.read_file()
        changed_lines = self.change_detector.parse_diffs(self.change_detector.get_file_diff(file_path, is_new_file))
        changes_in_pyfile = self.change_detector.identify_changes_in_structure(changed_lines, file_handler.get_functions_and_classes(source_code))
        logger.info(f"检测到变更对象：\n{changes_in_pyfile}")
        
        # 判断project_hierarchy.json文件中能否找到对应.py文件路径的项
        with open(self.project_manager.project_hierarchy, 'r', encoding='utf-8') as f:
            json_data = json.load(f)
        
        # 如果找到了对应文件
        if file_handler.file_path in json_data:
            # 更新json文件中的内容
            json_data[file_handler.file_path] = self.update_existing_item(json_data[file_handler.file_path], file_handler, changes_in_pyfile)
            # 将更新后的file写回到json文件中
            with open(self.project_manager.project_hierarchy, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=4, ensure_ascii=False)
            
            logger.info(f"已更新{file_handler.file_path}文件的json结构信息。")

            # 将变更部分的json文件内容转换成markdown内容
            markdown = file_handler.convert_to_markdown_file(file_path=file_handler.file_path)
            # 将markdown内容写入.md文件
            file_handler.write_file(os.path.join(CONFIG['Markdown_Docs_folder'], file_handler.file_path.replace('.py', '.md')), markdown)
            logger.info(f"已更新{file_handler.file_path}文件的Markdown文档。")

        # 如果没有找到对应的文件，就添加一个新的项
        else:
            self.add_new_item(file_handler,json_data)

        # 将run过程中更新的Markdown文件（未暂存）添加到暂存区
        git_add_result = self.change_detector.add_unstaged_files()
        
        if len(git_add_result) > 0:
            logger.info(f'已添加 {[file for file in git_add_result]} 到暂存区')
        
        # self.git_commit(f"Update documentation for {file_handler.file_path}") # 提交变更
        


if __name__ == "__main__":

    runner = Runner()
    
    # runner.meta_info.target_repo_hierarchical_tree.print_recursive()
    runner.run()

    logger.info("文档任务完成。")

