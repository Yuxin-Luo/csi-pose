"""QA manual audit — random/exhaustive frame extraction, overlay, single HTML gallery, gate aggregation.

Gate: teacher failure rate (false detection·joint collapse) < 2% (design §7) — 2.0% or higher = FAIL."""
import json
import re
from pathlib import Path

import cv2
import numpy as np

GATE_MAX = 0.02
STATUS_NAMES = {0: "ok", 1: "no_person", 2: "multi"}
# OpenPose BODY-18 limb — standard skeleton topology (same order as WiSPPN test_pam.py)
LIMBS18 = np.array([[0, 1], [0, 14], [0, 15], [14, 16], [15, 17], [1, 2], [1, 5],
                    [1, 8], [1, 11], [2, 3], [3, 4], [5, 6], [6, 7], [8, 9],
                    [9, 10], [11, 12], [12, 13]])

_HTML = """<!doctype html><meta charset="utf-8"><title>QA %%GID%%</title>
<style>body{background:#111;color:#eee;font:14px sans-serif;text-align:center}
img{max-width:96vw;max-height:80vh}#bar{margin:8px}.f{color:#f55}.p{color:#5f5}
button{margin:8px;padding:4px 12px}</style>
<div id="bar"></div><img id="im"><div id="st"></div>
<div>o = pass · x = fail · <-/-> navigate</div>
<button onclick="exp()">Export verdict JSON</button>
<script>
const ITEMS=%%ITEMS%%;
const KEY="qa-%%GID%%";let cur=0;
let V=JSON.parse(localStorage.getItem(KEY)||"{}");
function draw(){const it=ITEMS[cur];im.src=it.src;
 const v=V[it.f]||"";
 st.innerHTML=(cur+1)+"/"+ITEMS.length+" — frame "+it.f+" ["+it.status+"] "+
   (v?("Verdict: <b class="+(v=="fail"?"f":"p")+">"+v+"</b>"):"Unjudged");
 const n=Object.keys(V).length,nf=Object.values(V).filter(x=>x=="fail").length;
 bar.textContent="Verdict "+n+"/"+ITEMS.length+" · fail "+nf;}
function set(v){V[ITEMS[cur].f]=v;localStorage.setItem(KEY,JSON.stringify(V));
 if(cur<ITEMS.length-1)cur++;draw();}
function exp(){const out={_total:ITEMS.length};
 for(const it of ITEMS){if(V[it.f])out[it.f]=V[it.f];}
 const a=document.createElement("a");
 a.href=URL.createObjectURL(new Blob([JSON.stringify(out,null,1)],{type:"application/json"}));
 a.download="verdicts.json";a.click();}
addEventListener("keydown",e=>{if(e.key=="ArrowRight"&&cur<ITEMS.length-1){cur++;draw()}
 else if(e.key=="ArrowLeft"&&cur>0){cur--;draw()}
 else if(e.code=="KeyO")set("pass");else if(e.code=="KeyX")set("fail")});
draw();
</script>"""


def pick_frames(F, *, k=200, seed=7, all_frames=False):
    """Random K frames from all frames regardless of status (deterministic) — use all_frames=True for S04."""
    if all_frames or F <= k:
        return np.arange(F)
    rng = np.random.default_rng(seed)
    return np.sort(rng.choice(F, size=k, replace=False))


def _pti(p):
    """int coordinates for drawing — clip outliers exceeding int32 (prevent cv2 overflow)."""
    return tuple(np.clip(p, -1_000_000_000, 1_000_000_000).astype(int))


def render_overlay(frame, pose18, *, status, det_score):
    out = frame.copy()
    pts, conf = pose18[:, :2], pose18[:, 2]
    for a, b in LIMBS18:
        if np.isfinite(pts[a]).all() and np.isfinite(pts[b]).all():
            cv2.line(out, _pti(pts[a]), _pti(pts[b]), (200, 200, 0), 1)
    for k in range(18):
        if not np.isfinite(pts[k]).all():
            continue
        c = conf[k]
        col = (0, 200, 0) if c >= 0.5 else (0, 220, 220) if c >= 0.2 else (0, 0, 255)
        cv2.circle(out, _pti(pts[k]), 3, col, -1)
    txt = STATUS_NAMES.get(int(status), "?")
    if np.isfinite(det_score):
        txt += f" det={det_score:.2f}"
    cv2.putText(out, txt, (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                (0, 255, 0) if status == 0 else (0, 0, 255), 1)
    return out


def build_gallery(out_dir, mp4_path, pose18, status, det_score, idxs, *, gid):
    """Overlay JPEGs for selected frames + index.html — returns: html path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    want = {int(i) for i in idxs}
    items = []
    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        raise SystemExit(f"Failed to open mp4: {mp4_path}")
    try:
        f = 0
        while want:
            ok, frame = cap.read()
            if not ok:
                raise SystemExit(
                    f"mp4 ended before frame {min(want)} — label and video from different sessions?")
            if f in want:
                want.discard(f)
                img = render_overlay(frame, pose18[f], status=status[f],
                                     det_score=det_score[f])
                name = f"{f:06d}.jpg"
                cv2.imwrite(str(out / name), img)
                items.append({"f": f, "src": name,
                              "status": STATUS_NAMES.get(int(status[f]), "?")})
            f += 1
    finally:
        cap.release()
    items.sort(key=lambda d: d["f"])
    gid_safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(gid))
    html = (_HTML.replace("%%ITEMS%%", json.dumps(items).replace("</", "<\\/"))
            .replace("%%GID%%", gid_safe))
    page = out / "index.html"
    page.write_text(html, encoding="utf-8")
    return page


def aggregate(paths):
    """Sum verdict JSONs -> (per_file, judged, fails, rate).

    Empty/invalid/incomplete (judged < _total) verdicts are fail-loud — gate denominator integrity.

    """
    per, judged, fails = [], 0, 0
    for p in paths:
        v = json.loads(Path(p).read_text(encoding="utf-8"))
        total = v.pop("_total", None)
        bad = [k for k, s in v.items() if s not in ("pass", "fail")]
        if bad:
            raise SystemExit(f"{p}: Unknown verdict value {bad[:3]}")
        if total is not None and len(v) < int(total):
            raise SystemExit(
                f"{p}: Unjudged {int(total) - len(v)} frames — judge all frames o/x before exporting (gate denominator integrity)")
        f = sum(1 for s in v.values() if s == "fail")
        per.append((str(p), len(v), f))
        judged += len(v)
        fails += f
    if judged == 0:
        raise SystemExit("No verdicts — export from gallery after o/x judgment")
    return per, judged, fails, fails / judged


def gate_pass(rate):
    return rate < GATE_MAX
