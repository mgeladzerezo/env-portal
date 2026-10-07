"""Write the API's OpenAPI document to a file. The frontend generates its types from it."""

import json
import sys
from pathlib import Path

from portal.config import Settings
from portal.container import build_container
from portal.main import create_app


def main() -> None:
    target = Path(sys.argv[1])
    settings = Settings(_env_file=None, database_url="sqlite://", queue_backend="inline")
    app = create_app(build_container(settings))
    target.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
