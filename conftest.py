import sys
from pathlib import Path

# Ensure the project root is on sys.path so tests/ can import yt_dont_recommend.
sys.path.insert(0, str(Path(__file__).parent))
