#!/bin/bash
# Syntax check all Python files

cd "$(dirname "$0")/../.."

echo "Checking Python syntax..."
python -m py_compile scalperbot/*.py scalperbot/**/*.py 2>&1

if [ $? -eq 0 ]; then
    echo "All files OK"
else
    echo "Syntax errors found"
    exit 1
fi
