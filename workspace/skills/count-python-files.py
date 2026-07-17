"""
Skill: count-python-files
Description: Counts Python files in a directory
Created: 2026-07-17T12:31:34.145094
Version: 2
Changes in v2: Added line counting functionality
"""


import os

def main(directory='.'):
    """Count Python files in a directory with details."""
    files = [f for f in os.listdir(directory) if f.endswith('.py')]
    total_lines = 0
    
    for f in files:
        try:
            with open(os.path.join(directory, f)) as fp:
                total_lines += len(fp.readlines())
        except:
            pass
    
    return f'Found {len(files)} Python files in {directory} ({total_lines:,} total lines)'

