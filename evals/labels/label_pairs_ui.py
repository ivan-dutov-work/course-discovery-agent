"""Local web UI for labelling thread_pairs.yaml.

    uv run python evals/labels/label_pairs_ui.py

Edits only the `label:` line of each pair. The intended labels are never read.
"""
from __future__ import annotations

import argparse
import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

PATH = Path(__file__).parent / "thread_pairs.yaml"
LABELS = ("continue", "new_topic", "no_signal")
LABEL_LINE = re.compile(r"^(  label:)[ ]*\S+(.*)$")
LOCK = threading.Lock()


def load() -> list[dict]:
    return yaml.safe_load(PATH.read_text())


def save(item_id: str, label: str | None) -> None:
    if label is not None and label not in LABELS:
        raise ValueError(f"unknown label: {label}")
    value = "null" if label is None else label
    with LOCK:
        lines = PATH.read_text().splitlines(keepends=True)
        current = None
        for i, line in enumerate(lines):
            if line.startswith("- id: "):
                current = line[6:].strip()
            match = LABEL_LINE.match(line.rstrip("\n"))
            if match and current == item_id:
                lines[i] = f"{match.group(1)} {value}{match.group(2)}\n"
                PATH.write_text("".join(lines))
                return
        raise KeyError(item_id)


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, kind: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/api/items":
            self._send(json.dumps({"items": load()}).encode(), "application/json")
        else:
            self._send(PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            save(body["id"], body["label"])
        except (KeyError, ValueError) as error:
            self._send(json.dumps({"error": str(error)}).encode(), "application/json", 400)
            return
        self._send(b"{}", "application/json")

    def log_message(self, *args) -> None:
        pass


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Thread pair labels</title>
<style>
:root{--bg:#fafaf9;--card:#fff;--fg:#1c1917;--mute:#78716c;--line:#e7e5e4;--acc:#2563eb}
@media(prefers-color-scheme:dark){:root{--bg:#1c1917;--card:#292524;--fg:#fafaf9;--mute:#a8a29e;--line:#44403c;--acc:#60a5fa}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:0 16px 80px}
header{position:sticky;top:0;background:var(--bg);padding:12px 0;border-bottom:1px solid var(--line)}
.bar{height:4px;background:var(--line);margin-top:8px}.bar i{display:block;height:100%;background:var(--acc)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px;margin:12px 0}
.card.cur{border-color:var(--acc)}
.id,.hint{color:var(--mute);font-size:13px}
.ctx{margin:6px 0;color:var(--mute)}.msg{font-size:18px;margin:8px 0 12px}
button{font:inherit;padding:6px 14px;margin-right:8px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);cursor:pointer}
button.on{background:var(--acc);border-color:var(--acc);color:#fff}
kbd{margin-left:6px;font-size:12px;opacity:.7}
</style></head><body><main>
<header><strong>Thread pair labels</strong> <span id="count"></span>
<span class="hint">j/k move · c continue · n new topic · s no signal · u unlabelled</span><div class="bar"><i id="fill"></i></div></header>
<div id="list"></div></main>
<script>
let items=[],cur=0;
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function age(h){return h<1?Math.round(h*60)+' min':h<48?Math.round(h)+' hours':Math.round(h/24)+' days'}
async function load(){items=(await (await fetch('/api/items')).json()).items;cur=Math.max(0,items.findIndex(i=>i.label===null));render();}
function set(i,l){items[i].label=l;fetch('/api/save',{method:'POST',body:JSON.stringify({id:items[i].id,label:l})});render(false);}
function render(scroll=true){
 const done=items.filter(i=>i.label!==null).length;
 $('#count').textContent=done+' / '+items.length+' labelled';$('#fill').style.width=(100*done/items.length)+'%';
 $('#list').innerHTML=items.map((it,i)=>{
  const f=Object.entries(it.parked.filters||{}).map(([k,v])=>k+': '+v).join(', ');
  return `<div class="card ${i===cur?'cur':''}" id="c${i}" data-i="${i}"><div class="id">${it.id}</div>
  <div class="ctx">parked search: <b>${esc(it.parked.topic)}</b>${f?' ('+esc(f)+')':''}, last active ${age(it.parked.age_hours)} ago</div>
  <div class="msg">${esc(it.message)}</div>
  <button class="${it.label==='continue'?'on':''}" data-set="continue" data-i="${i}">Continue<kbd>c</kbd></button>
  <button class="${it.label==='new_topic'?'on':''}" data-set="new_topic" data-i="${i}">New topic<kbd>n</kbd></button>
  <button class="${it.label==='no_signal'?'on':''}" data-set="no_signal" data-i="${i}">No signal<kbd>s</kbd></button></div>`}).join('');
 if(scroll)$('#c'+cur)?.scrollIntoView({block:'center',behavior:'smooth'});}
document.addEventListener('click',e=>{const b=e.target.closest('[data-set]');if(b){cur=+b.dataset.i;set(cur,b.dataset.set);}});
document.addEventListener('keydown',e=>{
 if(e.metaKey||e.ctrlKey||e.altKey)return;
 const k=e.key;
 if(k==='j')cur=Math.min(items.length-1,cur+1);
 else if(k==='k')cur=Math.max(0,cur-1);
 else if(k==='c'||k==='n'||k==='s'){set(cur,{c:'continue',n:'new_topic',s:'no_signal'}[k]);cur=Math.min(items.length-1,cur+1);}
 else if(k==='u')set(cur,null);
 else return;
 render();});
load();
</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"labelling {PATH.name} at {url} (Ctrl+C to stop)")
    webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
