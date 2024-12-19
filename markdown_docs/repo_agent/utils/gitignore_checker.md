## ClassDef GitignoreChecker
**GitignoreChecker**: The GitignoreChecker class is designed to parse a .gitignore file and check if files and folders within a specified directory should be ignored according to the rules defined in that .gitignore file. It specifically supports checking for Python files (.py) that are not ignored.

attributes:
· directory: A string representing the path to the directory that needs to be checked against the .gitignore rules.
· gitignore_path: A string representing the path to the .gitignore file. If this file is not found, a default .gitignore located in the project's root directory will be used.

Code Description: The GitignoreChecker class initializes with a specified directory and .gitignore file path. It loads and parses the .gitignore file to extract patterns for ignoring files and folders. These patterns are then split into separate lists for folder and file patterns. The class provides functionality to check if specific paths (files or directories) should be ignored based on these patterns. Finally, it can generate a list of Python (.py) files within the specified directory that are not marked as ignored by the .gitignore rules.

Note: This class is particularly useful in scenarios where you need to programmatically determine which files should be included or excluded from operations such as version control, backups, or analysis. It ensures that files and folders explicitly listed for exclusion in a .gitignore file are correctly recognized and handled.

Output Example: A possible return value of the check_files_and_folders method could be:
['src/main.py', 'tests/test_module.py']
This output represents a list of Python files within the specified directory that are not ignored according to the rules defined in the .gitignore file. Each path is relative to the initial directory provided during the GitignoreChecker's initialization.
### FunctionDef __init__(self, directory, gitignore_path)
**__init__**: This function initializes an instance of the GitignoreChecker class by setting up the directory to be checked and loading patterns from a specified .gitignore file.

parameters:
· directory (str): The path to the directory that needs to be checked against the .gitignore rules.
· gitignore_path (str): The path to the .gitignore file that contains the ignore rules.

Code Description: Upon instantiation, the GitignoreChecker class is configured with two key pieces of information: the directory to check and the location of the .gitignore file. These are stored as instance variables `self.directory` and `self.gitignore_path`. The function then proceeds to load and parse the patterns from the .gitignore file by calling the `_load_gitignore_patterns` method. This method reads the content of the .gitignore file, handling cases where the specified file might not exist by falling back to a default path. Once the patterns are loaded, they are categorized into folder patterns (those ending with a slash) and file patterns (all others). These categories are stored as `self.folder_patterns` and `self.file_patterns`, respectively, and will be used throughout the lifecycle of the GitignoreChecker instance to determine which files and directories should be ignored based on the .gitignore rules.

Note: The initialization process is crucial for setting up the necessary context and patterns required by the GitignoreChecker. It ensures that both user-defined and default .gitignore configurations are supported, allowing the checker to accurately assess the contents of a directory against the specified ignore rules. This setup enables developers to easily integrate and use the GitignoreChecker in their projects to manage version control exclusions effectively.
***
### FunctionDef _load_gitignore_patterns(self)
**_load_gitignore_patterns**: This function loads and parses a .gitignore file to extract patterns that specify which files and directories should be ignored by version control systems like Git. It handles both user-specified paths and falls back to a default path if the specified file is not found.

parameters:
· None: This function does not take any direct parameters but relies on an instance variable `self.gitignore_path` set during initialization of the `GitignoreChecker` class.

Code Description: The function attempts to open and read the .gitignore file located at `self.gitignore_path`. If this file is not found, it catches the `FileNotFoundError` exception and instead reads from a default path constructed relative to the location of the current script. Once the content of the .gitignore file is retrieved, it passes this content to `_parse_gitignore`, which processes the content to extract meaningful patterns by removing comments and empty lines. These patterns are then passed to `_split_gitignore_patterns` to categorize them into folder patterns (those ending with a slash) and file patterns (all others). The function returns these two lists as a tuple.

Note: This function is crucial for initializing the `GitignoreChecker` class, as it sets up the necessary patterns used throughout the object's lifecycle to determine which files and directories should be ignored based on the .gitignore rules. It ensures that both user-defined and default .gitignore configurations are supported seamlessly.

Output Example: Given a .gitignore file with the following content:
```
# Ignore all logs
*.log

# Ignore build directory
/build/

# Ignore temporary files
*.tmp
```

The function would return `(['build'], ['*.log', '*.tmp'])`. Here, 'build/' is identified as a folder pattern and thus has its trailing slash removed before being added to the first list. '*.log' and '*.tmp' do not end with a slash, so they are added to the second list of file patterns.
***
### FunctionDef _parse_gitignore(gitignore_content)
**_parse_gitignore**: This function takes the content of a .gitignore file as input and returns a list of patterns extracted from it, excluding any comments and empty lines.

parameters:
· gitignore_content: A string representing the entire content of a .gitignore file.

Code Description: The function processes each line of the provided .gitignore content. It first splits the content into individual lines using `splitlines()`. For each line, it removes leading and trailing whitespace with `strip()`. If the resulting line is not empty and does not start with a hash symbol (`#`), which indicates a comment in .gitignore files, the line is added to the `patterns` list. This list is then returned as the output.

Note: The function is designed to handle typical .gitignore syntax by filtering out comments and blank lines, ensuring that only relevant patterns are included in the final list.

Output Example: If the input string is:
```
# Ignore all logs
*.log

# Ignore build directory
/build/

# Ignore temporary files
*.tmp
```

The output will be:
['*.log', '/build/', '*.tmp']
***
### FunctionDef _split_gitignore_patterns(gitignore_patterns)
**_split_gitignore_patterns**: This function takes a list of patterns from a .gitignore file and categorizes them into folder patterns and file patterns based on whether they end with a slash.

parameters:
· gitignore_patterns: A list containing strings, where each string is a pattern specified in the .gitignore file.

Code Description: The function initializes two empty lists, `folder_patterns` and `file_patterns`. It then iterates over each pattern in the provided `gitignore_patterns` list. If a pattern ends with a slash ("/"), it indicates that the pattern refers to a folder, so the trailing slash is removed using `rstrip("/")` and the cleaned pattern is added to the `folder_patterns` list. Patterns without a trailing slash are considered file patterns and are directly appended to the `file_patterns` list. After processing all patterns, the function returns a tuple containing the two lists.

Note: This function is typically used after loading and parsing the contents of a .gitignore file to separate the rules that apply to directories from those that apply to files. This separation can be useful for implementing more granular control over which files and directories are ignored by version control systems like Git.

Output Example: Given the input `['*.log', 'build/', 'temp/']`, the function would return `(['build', 'temp'], ['*.log'])`. Here, 'build/' and 'temp/' are identified as folder patterns and thus have their trailing slashes removed before being added to the first list. '*.log' does not end with a slash, so it is added to the second list of file patterns.
***
### FunctionDef _is_ignored(path, patterns, is_dir)
**_is_ignored**: This function checks if a given path matches any of the specified patterns, which can be used to determine if a file or directory should be ignored based on criteria such as those found in a .gitignore file.
parameters:
· path: A string representing the path (either file or directory) that needs to be checked against the provided patterns.
· patterns: A list containing the patterns to check the path against. These patterns can include wildcards and are typically used in .gitignore files.
· is_dir: A boolean indicating whether the given path is a directory (True) or not (False). This parameter helps in handling cases where a pattern specifically targets directories by ending with a slash.

Code Description: The function iterates over each pattern provided in the patterns list. For each pattern, it uses fnmatch.fnmatch to check if the path matches the pattern. If there is a match, the function immediately returns True, indicating that the path should be ignored based on the current pattern. Additionally, if the path represents a directory (as indicated by the is_dir parameter being True) and the pattern ends with a slash, it checks if the path matches the pattern without the trailing slash. This additional check ensures that patterns intended for directories are correctly applied even when the path does not end with a slash. If no patterns match the path after checking all provided patterns, the function returns False, indicating that the path is not ignored.

Note: The _is_ignored function is typically used internally within classes or modules dealing with file and directory management, such as in the context of version control systems like Git, where it helps determine which files and directories should be excluded from version tracking based on rules defined in a .gitignore file. It is designed to handle both files and directories by using the is_dir parameter.

Output Example: If the function is called with path="src/utils.py", patterns=["*.py", "docs/"], and is_dir=False, it would return True because the path matches the "*.py" pattern in the list of patterns. Conversely, if the path was "docs/index.html", the function would return False as it does not match any of the given patterns.
***
### FunctionDef check_files_and_folders(self)
**check_files_and_folders**: This function scans all files and directories within a specified directory to determine which Python (.py) files are not ignored according to patterns defined in a .gitignore file. It returns a list of relative paths to these non-ignored Python files.

parameters:
· None: The function does not take any direct parameters but relies on the `self.directory`, `self.folder_patterns`, and `self.file_patterns` attributes of the GitignoreChecker instance.

Code Description: The function initiates an empty list, `not_ignored_files`, to store paths of non-ignored Python files. It then walks through each directory and file in `self.directory` using `os.walk()`. For directories, it filters out those that match any pattern in `self.folder_patterns` by calling `_is_ignored()` with the `is_dir=True` flag. Similarly, for each file, it constructs a full path and checks if the file is not ignored according to `self.file_patterns` and has a `.py` extension. If both conditions are met, the relative path of the file to `self.directory` is added to `not_ignored_files`. Finally, the function returns this list.

Note: This function is typically used in scenarios where one needs to identify Python files that should be considered for processing or analysis, excluding those specified as ignored by a .gitignore file. It is particularly useful in projects involving code generation, static analysis, or version control operations.

Output Example: If the directory contains `src/main.py`, `docs/index.html`, and `tests/test_main.py` where only `main.py` and `test_main.py` are not ignored according to the .gitignore rules, the function would return `['src/main.py', 'tests/test_main.py']`.
***
