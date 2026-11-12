"""The Image Picker — keep or reject generated candidates, on any screen.

Same pattern as the Live Render Window: a zero-dependency page the
shell serves locally. Each candidate card shows the picture and its
seed, with Keep / Reject buttons in live mode. Keeping the first
candidate of a job-linked batch makes it that job's reference image;
rejecting deletes the file and records the seed so it never comes
back. A static snapshot (live=False) is a read-only record of a batch.
"""
from __future__ import annotations

import json
from pathlib import Path

PAGE_CSS = """
body { margin:0; font-family: system-ui, sans-serif; background:#17130f; color:#f2ede4; }
header { padding:14px 18px 6px; }
h1 { font-size:17px; margin:0 0 4px; }
.sub { opacity:.75; font-size:13px; }
.grid { display:flex; flex-wrap:wrap; gap:14px; padding:14px 18px 22px; }
.card { background:#241d16; border-radius:12px; padding:10px; width:264px; }
.card img { width:264px; height:264px; object-fit:cover; border-radius:8px; background:#0d0a08; display:block; }
.missing { width:264px; height:264px; border-radius:8px; background:#0d0a08; display:flex;
  align-items:center; justify-content:center; opacity:.6; font-size:13px; }
.meta { font-size:12px; opacity:.8; margin:8px 2px; }
.status { font-weight:600; }
.kept { color:#9ed08a; } .rejected { color:#e08a8a; } .failed { color:#e0a458; }
.row { display:flex; gap:8px; }
button { flex:1; padding:9px 0; border-radius:8px; border:0; font-size:14px; cursor:pointer; }
.keep { background:#3f6128; color:#eef7e6; } .reject { background:#5c2b23; color:#fbeae6; }
button:disabled { opacity:.4; cursor:default; }
"""

PAGE_SCRIPT = """
var LIVE = __LIVE__;
async function decide(file, decision, btn) {
  btn.disabled = true;
  try {
    var r = await fetch('/api/decide', {method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({file: file, decision: decision})});
    var data = await r.json();
    if (!data.ok) { alert(data.error || 'failed'); btn.disabled = false; return; }
    render(data.batch);
  } catch (e) { alert(String(e)); btn.disabled = false; }
}
function esc(s){ var d=document.createElement('div'); d.textContent=String(s==null?'':s); return d.innerHTML; }
function render(batch) {
  document.getElementById('prompt').textContent = batch.prompt;
  document.getElementById('sub').textContent =
    'Model: ' + (batch.model || '-') + '  -  batch ' + batch.slug +
    (batch.job ? '  -  job: ' + batch.job : '') +
    (batch.reference_file ? '  -  reference: ' + batch.reference_file : '');
  var grid = document.getElementById('grid');
  grid.innerHTML = '';
  batch.candidates.forEach(function(c) {
    var card = document.createElement('div'); card.className = 'card';
    var html = '';
    if (c.status === 'rejected') {
      html += '<div class="missing">rejected - deleted</div>';
    } else if (c.status === 'failed') {
      html += '<div class="missing">generation failed</div>';
    } else {
      html += '<img src="/img/' + encodeURIComponent(c.file) + '" alt="">';
    }
    html += '<div class="meta">seed ' + c.seed + ' - <span class="status ' +
      esc(c.status) + '">' + esc(c.status) + '</span></div>';
    if (LIVE && c.status === 'pending') {
      html += '<div class="row"><button class="keep">Keep</button>' +
        '<button class="reject">Reject</button></div>';
    }
    card.innerHTML = html;
    if (LIVE && c.status === 'pending') {
      var btns = card.querySelectorAll('button');
      btns[0].addEventListener('click', function(){ decide(c.file, 'keep', btns[0]); });
      btns[1].addEventListener('click', function(){ decide(c.file, 'reject', btns[1]); });
    }
    grid.appendChild(card);
  });
}
var INITIAL = __BATCH_JSON__;
render(INITIAL);
if (LIVE) {
  setInterval(async function(){
    try { var r = await fetch('/api/batch'); var d = await r.json();
      if (d.ok) render(d.batch); } catch (e) {}
  }, 4000);
}
"""


def render_page(batch: dict, live: bool = False) -> str:
    script = (PAGE_SCRIPT
              .replace("__LIVE__", "true" if live else "false")
              .replace("__BATCH_JSON__", json.dumps(batch)))
    return ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Image Picker</title><style>" + PAGE_CSS + "</style></head><body>"
            "<header><h1 id=\"prompt\"></h1><div class=\"sub\" id=\"sub\"></div></header>"
            "<div class=\"grid\" id=\"grid\"></div>"
            "<script>\n" + script + "\n</script></body></html>\n")


def _workspace_of(batch_dir: Path) -> Path:
    if batch_dir.parent.name == "images":
        return batch_dir.parent.parent
    return batch_dir.parent


def make_server(batch_dir: str | Path, host: str = "127.0.0.1", port: int = 8762):
    """HTTP server bound to ONE image batch.

    GET  /            the picker page
    GET  /api/batch   the current batch record (the page polls this)
    GET  /img/<file>  a candidate PNG (only files on the batch record)
    POST /api/decide  {file, decision: keep|reject}
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from . import imagegen

    bdir = Path(batch_dir)
    workspace = _workspace_of(bdir)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj: dict):
            self._send(code, (json.dumps(obj) + "\n").encode(), "application/json")

        def do_GET(self):
            try:
                if self.path == "/":
                    batch = imagegen.load_batch(bdir)
                    self._send(200, render_page(batch, live=True).encode(),
                               "text/html; charset=utf-8")
                elif self.path == "/api/batch":
                    self._json(200, {"ok": True, "batch": imagegen.load_batch(bdir)})
                elif self.path.startswith("/img/"):
                    name = Path(self.path[5:]).name
                    batch = imagegen.load_batch(bdir)
                    if name not in {c["file"] for c in batch["candidates"]}:
                        self._json(404, {"ok": False, "error": "not a candidate"})
                        return
                    target = bdir / name
                    if not target.exists():
                        self._json(404, {"ok": False, "error": "file gone"})
                        return
                    self._send(200, target.read_bytes(), "image/png")
                else:
                    self._json(404, {"ok": False, "error": "not found"})
            except Exception as exc:
                self._json(500, {"ok": False, "error": str(exc)})

        def do_POST(self):
            if self.path != "/api/decide":
                self._json(404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                batch = imagegen.decide(workspace, bdir, str(payload.get("file", "")),
                                        str(payload.get("decision", "")))
                self._json(200, {"ok": True, "batch": batch})
            except Exception as exc:
                self._json(400, {"ok": False, "error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


def serve(batch_dir: str | Path, host: str = "127.0.0.1", port: int = 8762) -> str:
    """Serve the picker for a batch; returns the URL. Blocks."""
    server = make_server(batch_dir, host, port)
    url = f"http://{host}:{server.server_address[1]}/"
    print(f"Image Picker: {url}")
    print(f"Batch: {batch_dir}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return url
