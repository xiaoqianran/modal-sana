from __future__ import annotations

import json
from typing import Any


def metadata_line(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, default=str)
