"""Exit non-zero if any file under schemas/ is not a valid draft 2020-12 schema or has an
unresolvable ``$id``. Wired into ci.yml."""

from __future__ import annotations

import sys

from veridian.contracts._schemas import check_all_schemas_valid


def main() -> int:
    problems = check_all_schemas_valid()
    if problems:
        print("Schema problems:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("All schemas valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
