"""Observed call events: a thread-local sink that HTTP clients and native loggers report to.

A track installs its own sink with ``diagnostic_context``; without one, ``emit`` does nothing.
Never synthesize product execution steps.
"""
import logging
import threading
from contextlib import contextmanager

_LOCAL = threading.local()
_SECRET = {"authorization", "proxy-authorization", "cookie", "set-cookie", "api_key", "apikey", "api-key", "x-api-key", "x-bb-api-key", "x-browser-use-api-key", "token", "password", "secret"}

def redact(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if str(k).lower() in _SECRET else redact(v) for k,v in value.items()}
    if isinstance(value, list): return [redact(v) for v in value]
    return value

def emit(event, **fields):
    sink=getattr(_LOCAL,"sink",None)
    if sink:sink.emit("caller",event,**fields)

class NativeHandler(logging.Handler):
    def emit(self, record):
        sink=getattr(_LOCAL,"sink",None)
        if sink:
            sink.emit("native_library", "logging_record", logger=record.name,level=record.levelname,message=record.getMessage(),file=record.pathname,line=record.lineno,
                      exception=self.formatter.formatException(record.exc_info) if record.exc_info and self.formatter else None)

@contextmanager
def diagnostic_context(sink, names=("urllib3","requests","langchain_community")):
    old=getattr(_LOCAL,"sink",None);_LOCAL.sink=sink
    handler=NativeHandler();handler.setFormatter(logging.Formatter())
    loggers=[logging.getLogger(name) for name in names]
    levels=[logger.level for logger in loggers]
    if sink:
        for logger in loggers:logger.setLevel(logging.DEBUG);logger.addHandler(handler)
    try:yield
    finally:
        if sink:
            for logger,level in zip(loggers,levels,strict=True):logger.removeHandler(handler);logger.setLevel(level)
        _LOCAL.sink=old
