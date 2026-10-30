"""Entry point: python -m target_analyzer [db_path]"""
import sys
from pathlib import Path

from .ui import App


def main():
    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "targets.db"
    App(db_path).mainloop()


if __name__ == "__main__":
    main()
