"""Load project configuration before database and crawler modules are imported."""
from pathlib import Path

from dotenv import load_dotenv

# Shell/Compose variables take precedence. The path does not depend on cwd.
load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
