#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Function to display error messages
error_exit() {
    echo "$1" >&2
    exit 1
}

# Check if repos.txt exists
if [[ ! -f "repos.txt" ]]; then
    error_exit "Error: repos.txt file not found."
fi

# Read repos.txt line by line
while IFS= read -r repo; do
    # Skip empty lines and lines starting with #
    [[ -z "$repo" || "$repo" =~ ^# ]] && continue

    echo "----------------------------------------"
    echo "Processing repository: $repo"
    echo "----------------------------------------"

    # Run repoagent clean
    if ! repoagent clean; then
        echo "Error: 'repoagent clean' failed for $repo. Skipping to the next repository."
        continue
    fi

    # Update target_repo in config.toml
    if ! sed -i 's|^\s*target_repo = ".*"|target_repo = "'"$repo"'"|' config.toml; then
        echo "Error: Failed to update 'target_repo' in config.toml for $repo. Skipping to the next repository."
        continue
    fi

    echo "Updated 'target_repo' in config.toml to: $repo"

    # Run repoagent run
    if ! repoagent run; then
        echo "Error: 'repoagent run' failed for $repo. Skipping to the next repository."
        continue
    fi

    echo "Successfully processed $repo"
    echo ""

done < repos.txt

echo "All repositories have been processed."
