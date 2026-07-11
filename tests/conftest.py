import sys
from pathlib import Path

# Allow `import vuz_monitor` without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
