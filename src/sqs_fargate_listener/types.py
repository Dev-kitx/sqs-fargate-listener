from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from typing import Any


@dataclass
class SqsMessage:
    message_id: str
    receipt_handle: str
    body: str
    attributes: dict[str, Any]
    md: dict[str, Any]  # raw boto fields (includes MessageAttributes when requested)

    @cached_property
    def json(self) -> Any:
        """
        Parse Body as JSON (cached). Raises json.JSONDecodeError if invalid.
        Usage:
            data = msg.json
        """
        return json.loads(self.body)

    def try_json(self) -> tuple[Any | None, Exception | None]:
        """
        Safe JSON parse. Returns (data, error). Never raises.
        Usage:
            data, err = msg.try_json()
        """
        try:
            return json.loads(self.body), None
        except Exception as e:
            return None, e

    def message_attributes(self) -> dict[str, Any]:
        """
        Return simplified MessageAttributes (str/number/binary string values).
        Assumes ReceiveMessage included MessageAttributeNames=["All"].
        """
        raw = self.md.get("MessageAttributes") or {}
        out: dict[str, Any] = {}
        for k, v in raw.items():
            # SQS can have StringValue, BinaryValue, or StringListValue/NumberListValue (rare)
            if "StringValue" in v:
                out[k] = v["StringValue"]
            elif "BinaryValue" in v:
                # Keep bytes as-is; caller can decode if needed
                out[k] = v["BinaryValue"]
            else:
                # Fallback to raw
                out[k] = v
        return out


@dataclass
class BatchResult:
    failed_receipt_handles: list[str]
