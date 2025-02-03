from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict, List

from colorama import Fore, Style

# -------------------------------------
# Status constants for clarity
# -------------------------------------
STATUS_PENDING = 0
STATUS_IN_PROGRESS = 1
STATUS_COMPLETED = 2
STATUS_ERROR = 3


class Task:
    def __init__(self, task_id: int, dependencies: List[Task], extra_info: Any = None):
        self.task_id = task_id
        self.extra_info = extra_info
        self.dependencies = dependencies
        self.status = STATUS_PENDING  # start as pending


class TaskManager:
    def __init__(self):
        """
        Initializes the TaskManager.

        Attributes:
        - task_dict (Dict[int, Task]): Maps task IDs to Task objects.
        - task_lock (threading.Lock): Ensures thread-safe access to task_dict.
        - now_id (int): Tracks the next unused task ID.
        - query_id (int): Counts how many times a worker has requested a task.
        """
        self.task_dict: Dict[int, Task] = {}
        self.task_lock = threading.Lock()
        self.now_id = 0
        self.query_id = 0

    @property
    def all_success(self) -> bool:
        # True if no tasks remain; we consider that "success" for this demo.
        return len(self.task_dict) == 0

    def add_task(self, dependency_task_ids: List[int], extra=None) -> int:
        """
        Adds a new task to the TaskManager.
        :param dependency_task_ids: IDs of tasks that must complete before this one.
        :param extra: Optional extra data or function.
        :return: The newly assigned task ID.
        """
        with self.task_lock:
            depend_tasks = [self.task_dict[tid] for tid in dependency_task_ids]
            self.task_dict[self.now_id] = Task(
                task_id=self.now_id,
                dependencies=depend_tasks,
                extra_info=extra
            )
            self.now_id += 1
            return self.now_id - 1

    def get_next_task(self, process_id: int):
        """
        Returns the next task ready to run (no remaining dependencies),
        marking it as in-progress. If no task is ready, returns (None, -1).
        """
        with self.task_lock:
            self.query_id += 1
            for tid, task in self.task_dict.items():
                ready = (len(task.dependencies) == 0) and (task.status == STATUS_PENDING)
                if ready:
                    task.status = STATUS_IN_PROGRESS
                    print(
                        f"{Fore.RED}[process {process_id}]{Style.RESET_ALL}: "
                        f"get task({tid}), remain({len(self.task_dict)})"
                    )
                    return task, tid

            return None, -1

    def mark_completed(self, task_id: int):
        """
        Marks the specified task as completed and removes it from task_dict.
        Also removes it from the dependencies of any remaining tasks.
        """
        with self.task_lock:
            if task_id not in self.task_dict:
                return
            target_task = self.task_dict[task_id]
            for t in self.task_dict.values():
                if target_task in t.dependencies:
                    t.dependencies.remove(target_task)
            self.task_dict.pop(task_id, None)


def worker(task_manager: TaskManager, process_id: int, handler: Callable[[Any], None]):
    """
    Worker function. Continuously fetches tasks from the TaskManager. 
    Runs them if available; otherwise, sleeps briefly and checks again.
    """
    while True:
        if task_manager.all_success:
            break

        task, task_id = task_manager.get_next_task(process_id)
        if task is None:
            time.sleep(0.5)
            continue

        handler(task.extra_info)
        task_manager.mark_completed(task.task_id)


def default_handler(extra):
    """
    A default function that knows how to handle 'extra' if it's callable.
    """
    if callable(extra):
        extra()


if __name__ == "__main__":
    task_manager = TaskManager()

    def some_function():
        # Emulate real work by sleeping randomly up to 3 seconds
        time.sleep(random.random() * 3)

    # Correct usage of add_task: first arg is dependency IDs, second is the "extra" item
    i1 = task_manager.add_task([], some_function)
    i2 = task_manager.add_task([], some_function)
    i3 = task_manager.add_task([i1], some_function)
    i4 = task_manager.add_task([i2, i3], some_function)
    i5 = task_manager.add_task([i2, i3], some_function)
    i6 = task_manager.add_task([i1], some_function)

    # Create multiple worker threads, each with a distinct process_id
    threads = []
    for i in range(4):
        t = threading.Thread(target=worker, args=(task_manager, i, default_handler))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()
