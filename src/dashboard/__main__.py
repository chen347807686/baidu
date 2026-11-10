import sys

import uvicorn

from dashboard.app import create_app
from dashboard.config import ConfigurationError, load_settings


def main() -> int:
    try:
        settings = load_settings()
        app = create_app()
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
