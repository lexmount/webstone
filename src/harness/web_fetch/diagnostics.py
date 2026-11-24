"""Observed call diagnostics: never synthesize product execution steps."""
import base64
import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from harness.call_diagnostics import redact

class CallDiagnostics:
    def __init__(self, path, call_id):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.call_id=call_id;self.lock=threading.Lock();self.closed=False
        self.file=self.path.open("x",encoding="utf-8")
    def emit(self, source, event, **fields):
        with self.lock:
            if self.closed:return
            row={"at":datetime.now(timezone.utc).isoformat(),"call_id":self.call_id,"source":source,"event":event,**redact(fields)}
            self.file.write(json.dumps(row,ensure_ascii=False,default=str)+"\n");self.file.flush()
    def finish(self, result):
        self.emit("caller", "collection_completed", ok=result.ok, error_type=result.error_type,
                  error_message=result.error_message)
        with self.lock:
            self.closed=True;self.file.close()
        raw=self.path.read_bytes()
        product_file=Path(str(self.path)+".openclaw.log")
        if product_file.is_file():
            raw+=b"\n--- native OpenClaw file log (verbatim) ---\n"+product_file.read_bytes()
        product_stderr=Path(str(self.path)+".moli.stderr")
        if product_stderr.is_file():
            raw+=b"\n--- native Moli stderr (verbatim, through collection cutoff) ---\n"+product_stderr.read_bytes()
        native=result.metadata.pop("_diagnostic_log_b64", None)
        if native is not None:
            native=base64.b64decode(native,validate=True)
            if hashlib.sha256(native).hexdigest()!=result.metadata.get("diagnostic_log_sha256") or len(native)!=result.metadata.get("diagnostic_log_bytes"):
                raise ValueError("Native diagnostic bytes changed")
            if not product_stderr.is_file(): raw+=b"\n--- native product stderr (verbatim) ---\n"+native
        result.metadata.update(_diagnostic_log_b64=base64.b64encode(raw).decode(),diagnostic_log_sha256=hashlib.sha256(raw).hexdigest(),diagnostic_log_bytes=len(raw),diagnostic_log_truncated=bool(result.metadata.get("diagnostic_log_truncated",False)),diagnostic_log_sources="observed caller events and exposed native process/library logs")
