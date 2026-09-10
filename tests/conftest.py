import sys
from pathlib import Path

# Make the top-level packages (gateway, runtime, tools) importable regardless
# of how pytest is invoked.
sys.path.insert(0, str(Path(__file__).parent.parent))
