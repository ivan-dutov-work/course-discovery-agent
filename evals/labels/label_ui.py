"""Local web UI for labelling judge_notes.yaml.

    uv run python evals/labels/label_ui.py

Edits only the `fails:` line of each item, so the header and trailing comments stay intact.
The intended labels are never read.
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

PATH = Path(__file__).parent / "judge_notes.yaml"
CRITERIA = ["captured", "polarity", "scope", "no_invention", "no_loss"]
FAILS_LINE = re.compile(r"^(  fails:)[ ]*(?:\[[^\]]*\]|null)(.*)$")
LOCK = threading.Lock()


def load() -> list[dict]:
    return yaml.safe_load(PATH.read_text())


def save(item_id: str, fails: list[str] | None) -> None:
    if fails is not None and not set(fails) <= set(CRITERIA):
        raise ValueError(f"unknown criteria: {fails}")
    value = "null" if fails is None else "[" + ", ".join(c for c in CRITERIA if c in fails) + "]"
    with LOCK:
        lines = PATH.read_text().splitlines(keepends=True)
        current = None
        for i, line in enumerate(lines):
            if line.startswith("- id: "):
                current = line[6:].strip()
            match = FAILS_LINE.match(line.rstrip("\n"))
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
            payload = {"criteria": CRITERIA, "items": load()}
            self._send(json.dumps(payload).encode(), "application/json")
        else:
            self._send(PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        try:
            save(body["id"], body["fails"])
        except (KeyError, ValueError) as error:
            self._send(json.dumps({"error": str(error)}).encode(), "application/json", 400)
            return
        self._send(b"{}", "application/json")

    def log_message(self, *args) -> None:
        pass


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Judge labels</title>
<style>
:root{--bg:#fafaf9;--card:#fff;--fg:#1c1917;--mute:#78716c;--line:#e7e5e4;--acc:#2563eb;--ok:#16a34a;--bad:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#1c1917;--card:#292524;--fg:#fafaf9;--mute:#a8a29e;--line:#44403c;--acc:#60a5fa;--ok:#4ade80;--bad:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:720px;margin:0 auto;padding:16px}
header{position:sticky;top:0;background:var(--bg);padding:12px 0;z-index:1}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:var(--acc)}
.top{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:12px 0}
.card.cur{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc)}
.id{color:var(--mute);font-size:12px}.fb{font-size:17px;font-weight:600;margin:4px 0 12px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}@media(max-width:560px){.cols{grid-template-columns:1fr}}
h4{margin:0 0 4px;font-size:12px;text-transform:uppercase;color:var(--mute);letter-spacing:.04em}
ul{margin:0;padding:0;list-style:none}li{padding:3px 8px;border-radius:6px;margin:2px 0;background:var(--bg)}
li.add{background:color-mix(in srgb,var(--ok) 18%,transparent)}li.del{background:color-mix(in srgb,var(--bad) 18%,transparent);text-decoration:line-through}
li.empty{color:var(--mute);font-style:italic}
.opts{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px;align-items:center}
button,label.chip{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--fg);padding:6px 12px;border-radius:999px;cursor:pointer}
label.chip input{display:none}label.chip.on{background:var(--bad);border-color:var(--bad);color:#fff}
button.correct.on{background:var(--ok);border-color:var(--ok);color:#fff}
kbd{font:11px monospace;opacity:.6;margin-left:4px}
.hint{color:var(--mute);font-size:13px}
</style></head><body><main>
<header><div class="top"><strong>Judge labels</strong><span id="count"></span>
<span class="hint">j/k move · 0 correct · 1-5 toggle · u unlabelled</span></div><div class="bar"><i id="fill"></i></div></header>
<div id="list"></div></main>
<script>
let criteria=[],items=[],cur=0;
const $=s=>document.querySelector(s);
const esc=s=>s.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function load(){const r=await (await fetch('/api/items')).json();criteria=r.criteria;items=r.items;
 cur=Math.max(0,items.findIndex(i=>i.fails===null));render();}
async function save(it){await fetch('/api/save',{method:'POST',body:JSON.stringify({id:it.id,fails:it.fails})});}
function setFails(i,f){items[i].fails=f;save(items[i]);render(false);}
function toggle(i,c){const it=items[i];const s=new Set(it.fails||[]);s.has(c)?s.delete(c):s.add(c);setFails(i,[...s]);}
function lis(list,other,cls){if(!list.length)return '<li class="empty">(none)</li>';
 return list.map(n=>`<li class="${other.includes(n)?'':cls}">${esc(n)}</li>`).join('');}
function render(scroll=true){
 const done=items.filter(i=>i.fails!==null).length;
 $('#count').textContent=`${done} / ${items.length} labelled`;$('#fill').style.width=(100*done/items.length)+'%';
 $('#list').innerHTML=items.map((it,i)=>{const f=it.fails;
  const chips=criteria.map((c,k)=>`<label class="chip ${f&&f.includes(c)?'on':''}"><input type="checkbox" data-i="${i}" data-c="${c}">${c}<kbd>${k+1}</kbd></label>`).join('');
  return `<div class="card ${i===cur?'cur':''}" id="c${i}" data-i="${i}"><div class="id">${it.id}${f===null?'':' · labelled'}</div>
  <div class="fb">${esc(it.feedback)}</div>
  <div class="cols"><div><h4>notes before</h4><ul>${lis(it.notes_before,it.notes_after,'del')}</ul></div>
  <div><h4>notes after</h4><ul>${lis(it.notes_after,it.notes_before,'add')}</ul></div></div>
  <div class="opts"><button class="correct ${f&&!f.length?'on':''}" data-correct="${i}">Correct<kbd>0</kbd></button>${chips}</div></div>`}).join('');
 if(scroll)$('#c'+cur)?.scrollIntoView({block:'center',behavior:'smooth'});}
document.addEventListener('click',e=>{
 const card=e.target.closest('.card');if(card){cur=+card.dataset.i;document.querySelectorAll('.card').forEach((c,i)=>c.classList.toggle('cur',i===cur));}
 const b=e.target.closest('[data-correct]');if(b){setFails(+b.dataset.correct,[]);}});
document.addEventListener('change',e=>{const d=e.target.dataset;if(d.c)toggle(+d.i,d.c);});
document.addEventListener('keydown',e=>{
 if(e.metaKey||e.ctrlKey||e.altKey)return;
 if(e.key==='j'||e.key==='ArrowDown'){cur=Math.min(items.length-1,cur+1);render();e.preventDefault();}
 else if(e.key==='k'||e.key==='ArrowUp'){cur=Math.max(0,cur-1);render();e.preventDefault();}
 else if(e.key==='0'){setFails(cur,[]);cur=Math.min(items.length-1,cur+1);render();}
 else if(/^[1-5]$/.test(e.key)){toggle(cur,criteria[+e.key-1]);}
 else if(e.key==='u'){const n=items.findIndex((it,i)=>i>cur&&it.fails===null);const m=n<0?items.findIndex(it=>it.fails===null):n;if(m>=0){cur=m;render();}}});
load();
</script></body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"labelling {PATH.name} at {url} (Ctrl+C to stop)")
    webbrowser.open(url)
    server.serve_forever()


if __name__ == "__main__":
    main()
