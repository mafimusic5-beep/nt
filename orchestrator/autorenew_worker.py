#!/usr/bin/env python3
import json
import logging
import sys

from autorenew import ensure_autorenew_storage, process_autorenewals

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def main() -> int:
    ensure_autorenew_storage()
    result = process_autorenewals()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
