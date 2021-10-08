# Copyright 2019 The Matrix.org Foundation C.I.C.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Log formatters that output terse JSON.
"""
import json
import logging
import traceback

_encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))

# The properties of a standard LogRecord that should be ignored when generating
# JSON logs.
_IGNORED_LOG_RECORD_ATTRIBUTES = {
    "args",
    "asctime",
    "created",
    "exc_info",
    # exc_text isn't a public attribute, but is used to cache the result of formatException.
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}


def _extract_exc_info(record: logging.LogRecord) -> dict:
    exc_info = {}

    exc_type, exc_value, exc_tb = record.exc_info

    if exc_type:
        exc_info["exc_type"] = f"{exc_type.__name__}"
        exc_info["exc_value"] = f"{exc_value}"

        frame_summary = traceback.extract_tb(exc_tb, limit=1)[0]
        exc_info["exc_filename"] = frame_summary.filename
        exc_info["exc_lineno"] = frame_summary.lineno

    return exc_info


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "log": record.getMessage(),
            "namespace": record.name,
            "level": record.levelname,
        }

        return self._format(record, event)

    def _format(self, record: logging.LogRecord, event: dict) -> str:
        # Add attributes specified via the extra keyword to the logged event.
        for key, value in record.__dict__.items():
            if key not in _IGNORED_LOG_RECORD_ATTRIBUTES:
                event[key] = value

        if record.exc_info:
            event.update(_extract_exc_info(record))

        return _encoder.encode(event)


class TerseJsonFormatter(JsonFormatter):
    def format(self, record: logging.LogRecord) -> str:
        event = {
            "log": record.getMessage(),
            "namespace": record.name,
            "level": record.levelname,
            "time": round(record.created, 2),
        }

        return self._format(record, event)
