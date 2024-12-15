#!/bin/bash

# Exit immediately if a command exits with a non-zero status,
# if an undefined variable is used, or if any command in a pipeline fails
set -euo pipefail

# Function to display error messages and exit
error_exit() {
    echo "Error: $1" >&2
    exit 1
}

# Check if repos.txt exists
if [[ ! -f "repos.txt" ]]; then
    error_exit "repos.txt file not found."
fi

# Function to process a single repository
process_repo() {
    local repo="$1"

    echo "----------------------------------------"
    echo "Processing repository: $repo"
    echo "----------------------------------------"

    # Run repoagent clean
    if ! repoagent clean; then
        echo "Error: 'repoagent clean' failed for $repo. Skipping to the next repository."
        return
    fi

    # Run repoagent run with specified flags
    if ! repoagent run \
        --model "llama3.3:latest" \
        --temperature 0.2 \
        --request-timeout 180 \
        --base-url "http://localhost:11434/" \
        --target-repo-path "$repo" \
	--log-level "INFO" \
        --language "English" \
        --print-hierarchy \
        --max-thread-count 3; then
        echo "Error: 'repoagent run' failed for $repo. Skipping to the next repository."
        return
    fi

    echo "Successfully processed $repo"
    echo ""
}

# Read repos.txt line by line
while IFS= read -r repo || [[ -n "$repo" ]]; do
    # Trim leading/trailing whitespace
    repo="$(echo "$repo" | xargs)"

    # Skip empty lines and lines starting with #
    if [[ -z "$repo" || "$repo" =~ ^# ]]; then
        continue
    fi

    process_repo "$repo"
done < "repos.txt"

echo "All repositories have been processed."
