"""Record a scripted walk-through of the DataDistill web app as an MP4, with captions.

Edge (or Chrome) runs headless with its own empty profile and is driven over the Chrome DevTools Protocol:
the page's own screencast gives the frames, a drawn pointer moves to every element before it is clicked, waits for
processing are cut out of the video (with a short "… later" note), and captions are burned in with ffmpeg (libass).
Nothing is installed: needs Python `websockets` (already a dependency of the server) and ffmpeg.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

BROWSERS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe"]
W, H = 1440, 900

# A drawn pointer and click ripple: a headless screencast shows no mouse.
CURSOR_JS = r"""
(() => {
  if (window.__demo) return;
  const css = `#__cur{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
    transition:transform .65s cubic-bezier(.25,.8,.3,1);transform:translate(720px,450px)}
    #__cur svg{filter:drop-shadow(0 2px 3px rgba(0,0,0,.35))}
    .__rip{position:fixed;width:34px;height:34px;margin:-17px 0 0 -17px;border-radius:50%;z-index:2147483646;
    pointer-events:none;border:3px solid #0082F0;animation:__r .5s ease-out forwards}
    @keyframes __r{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}`;
  const add = () => {
    if (document.getElementById('__cur')) return;
    const st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);
    const c = document.createElement('div'); c.id = '__cur';
    c.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l16 10-7 1.5L9.5 21z" fill="#0c0c0c" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.body.appendChild(c);
    const p = window.__demoPos || [720, 450]; c.style.transition = 'none';
    c.style.transform = `translate(${p[0]}px,${p[1]}px)`; c.offsetWidth; c.style.transition = '';
  };
  const visible = el => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 &&
      getComputedStyle(el).visibility !== 'hidden'; };
  const find = (q) => {
    if (q.css) { const all = [...document.querySelectorAll(q.css)].filter(visible); return all[q.nth || 0] || null; }
    const tags = q.tags || 'button,a,[role=tab],[role=button],label,summary,input,select,h1,h2,h3,span,div,p,td,li';
    const want = q.text.toLowerCase();
    const cands = [...document.querySelectorAll(tags)].filter(visible).filter(e => {
      const t = (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().toLowerCase();
      return q.exact ? t === want : t.includes(want);
    });
    // the innermost match (a button inside a div that also contains the text)
    const inner = cands.filter(e => !cands.some(o => o !== e && e.contains(o)));
    const n = q.nth || 0;                      // negative: count from the end (the last match is the one in a dialog)
    return inner[n < 0 ? inner.length + n : n] || null;
  };
  const move = (x, y) => { add(); window.__demoPos = [x, y];
    document.getElementById('__cur').style.transform = `translate(${x - 4}px,${y - 2}px)`; };
  const ripple = (x, y) => { const r = document.createElement('div'); r.className = '__rip';
    r.style.left = x + 'px'; r.style.top = y + 'px'; document.body.appendChild(r); setTimeout(() => r.remove(), 600); };
  window.__demo = { add, find, move, ripple };
  if (document.readyState !== 'loading') add(); else document.addEventListener('DOMContentLoaded', add);
})();
"""


class Recorder:
    def __init__(self, out: Path, port=None):
        import socket
        self.out = Path(out)
        if port is None:                       # a free port, so a browser left over from another run is never used
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
        self.port = port
        self.tmp = Path(tempfile.mkdtemp(prefix="dd-demo-"))
        self.frames = []            # (virtual seconds, jpeg path)
        self.captions = []          # [start, end, title, text]
        self.notes = []             # [start, end, text] small top-right notes (time skips)
        self.paused_total = 0.0
        self.paused_at = None
        self.t0 = None
        self._id = 0
        self._pending = {}
        self.proc = None

    # ------------------------------------------------------------------ browser + protocol
    async def start(self, url):
        exe = next(p for p in BROWSERS if os.path.exists(p))
        prof = self.tmp / "profile"
        self.proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={self.port}",
                                      f"--user-data-dir={prof}", f"--window-size={W},{H}", "--hide-scrollbars",
                                      "--force-device-scale-factor=1", "--no-first-run", "--no-default-browser-check",
                                      "--disable-extensions", "--mute-audio", "about:blank"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=1))
                page = next(t for t in tabs if t.get("type") == "page")
                break
            except Exception:
                await asyncio.sleep(0.2)
        else:
            raise RuntimeError("browser did not start")
        self.ws = await websockets.connect(page["webSocketDebuggerUrl"], max_size=None)
        self.reader = asyncio.create_task(self._read())
        await self.cmd("Page.enable")
        await self.cmd("Runtime.enable")
        await self.cmd("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
        await self.cmd("Page.addScriptToEvaluateOnNewDocument", source=CURSOR_JS)
        await self.goto(url, settle=1.0)
        self.t0 = time.monotonic()
        await self.cmd("Page.startScreencast", format="jpeg", quality=88, maxWidth=W, maxHeight=H, everyNthFrame=1)

    async def _read(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self._pending:
                self._pending.pop(m["id"]).set_result(m)
            elif m.get("method") == "Page.screencastFrame":
                p = m["params"]
                asyncio.create_task(self.cmd("Page.screencastFrameAck", sessionId=p["sessionId"]))
                if self.t0 is not None and self.paused_at is None:
                    f = self.tmp / f"f{len(self.frames):06d}.jpg"
                    f.write_bytes(base64.b64decode(p["data"]))
                    self.frames.append((self.now(), f))

    async def cmd(self, method, **params):
        self._id += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[self._id] = fut
        await self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        m = await asyncio.wait_for(fut, 120)
        if "error" in m:
            raise RuntimeError(f"{method}: {m['error']}")
        return m.get("result", {})

    async def js(self, expr, await_promise=True):
        r = await self.cmd("Runtime.evaluate", expression=expr, awaitPromise=await_promise, returnByValue=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {r['exceptionDetails'].get('text')} {expr[:120]}")
        return r.get("result", {}).get("value")

    def now(self):
        if self.t0 is None:
            return 0.0
        end = self.paused_at if self.paused_at is not None else time.monotonic()
        return end - self.t0 - self.paused_total

    # ------------------------------------------------------------------ recording control
    def pause(self):
        if self.paused_at is None:
            self.paused_at = time.monotonic()

    def resume(self):
        if self.paused_at is not None:
            self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = None

    async def hold(self, s):
        await asyncio.sleep(s)

    def caption(self, title, text=""):
        t = self.now()
        if self.captions and self.captions[-1][1] is None:
            self.captions[-1][1] = t
        self.captions.append([t, None, title, text])

    def end_caption(self):
        if self.captions and self.captions[-1][1] is None:
            self.captions[-1][1] = self.now()

    def note(self, text, dur=2.5):
        t = self.now()
        self.notes.append([t, t + dur, text])

    # ------------------------------------------------------------------ actions
    async def goto(self, url, settle=1.2):
        await self.cmd("Page.navigate", url=url)
        await self.wait_js("document.readyState === 'complete'", 30)
        await asyncio.sleep(settle)

    async def wait_js(self, cond, timeout=30, every=0.25):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                if await self.js(f"!!({cond})"):
                    return True
            except RuntimeError:
                pass
            await asyncio.sleep(every)
        raise TimeoutError(cond)

    async def wait_text(self, text, timeout=30):
        return await self.wait_js(f"document.body.innerText.includes({json.dumps(text)})", timeout)

    async def wait_cut(self, cond, timeout=1800, later="Later"):
        """Wait for a slow step (processing) without filming it: the video jumps ahead with a short note."""
        self.pause()
        try:
            t0 = time.monotonic()
            await self.wait_js(cond, timeout, every=2)
            waited = time.monotonic() - t0
        finally:
            await asyncio.sleep(0.8)
            self.resume()
        mins = waited / 60
        self.note(f"{later}: {mins:.0f} min later" if mins >= 1 else f"{later}: {waited:.0f} s later")
        return waited

    async def wait_cut_fn(self, fn, timeout=3600, later="Later", every=3):
        """Like wait_cut, with a Python check (e.g. the app's API)."""
        self.pause()
        t0 = time.monotonic()
        try:
            while not fn():
                if time.monotonic() - t0 > timeout:
                    raise TimeoutError(later)
                await asyncio.sleep(every)
        finally:
            await asyncio.sleep(0.8)
            self.resume()
        waited = time.monotonic() - t0
        mins = waited / 60
        self.note(f"{later}: {mins:.0f} min later" if mins >= 1 else f"{later}: {waited:.0f} s later")
        return waited

    async def select(self, value_text, *, css="select", nth=0):
        """Choose the option whose text contains value_text."""
        q = self._q(None, nth, False, css)
        await self.point(css=css, nth=nth)
        await self.js(f"(() => {{ const el = window.__demo.find({q}); const o = [...el.options].find(o => o.text.includes({json.dumps(value_text)}));"
                      f" Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(el, o.value);"
                      f" el.dispatchEvent(new Event('change', {{bubbles: true}})); }})()")
        await asyncio.sleep(0.6)

    def _q(self, target, nth=0, exact=False, css=None, tags=None):
        q = {"nth": nth, "exact": exact}
        if css:
            q["css"] = css
        else:
            q["text"] = target
        if tags:
            q["tags"] = tags
        return json.dumps(q)

    async def point(self, target=None, *, css=None, nth=0, exact=False, tags=None, timeout=15, scroll=True):
        """Move the pointer to an element (scrolling it into view). Returns its centre."""
        q = self._q(target, nth, exact, css, tags)
        await self.wait_js(f"window.__demo && window.__demo.find({q})", timeout)
        if scroll:
            await self.js(f"window.__demo.find({q}).scrollIntoView({{block:'center', behavior:'smooth'}})")
            await asyncio.sleep(0.6)
        pos = await self.js(f"(() => {{ const r = window.__demo.find({q}).getBoundingClientRect(); "
                            f"const x = r.left + Math.min(r.width / 2, 60), y = r.top + r.height / 2; "
                            f"window.__demo.move(x, y); return [x, y]; }})()")
        await asyncio.sleep(0.75)
        return pos

    async def click(self, target=None, *, css=None, nth=0, exact=False, tags=None, timeout=15, after=0.8):
        q = self._q(target, nth, exact, css, tags)
        x, y = await self.point(target, css=css, nth=nth, exact=exact, tags=tags, timeout=timeout)
        await self.js(f"window.__demo.ripple({x}, {y}); window.__demo.find({q}).click()")
        await asyncio.sleep(after)

    async def type(self, text, *, css="input", nth=0, per_char=0.045, clear=True):
        q = self._q(None, nth, False, css)
        await self.point(css=css, nth=nth)
        await self.js(f"window.__demo.find({q}).focus()")
        setter = ("(el, v) => { const p = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;"
                  " Object.getOwnPropertyDescriptor(p, 'value').set.call(el, v); el.dispatchEvent(new Event('input', {bubbles: true})); }")
        cur = "" if clear else await self.js(f"window.__demo.find({q}).value")
        for ch in text:
            cur += ch
            await self.js(f"({setter})(window.__demo.find({q}), {json.dumps(cur)})")
            await asyncio.sleep(per_char)
        await asyncio.sleep(0.4)

    async def press_enter(self, *, css="input", nth=0):
        q = self._q(None, nth, False, css)
        await self.js(f"(() => {{ const el = window.__demo.find({q}); const f = el.form; "
                      f"if (f) f.requestSubmit(); else el.dispatchEvent(new KeyboardEvent('keydown', {{key: 'Enter', bubbles: true}})); }})()")
        await asyncio.sleep(0.5)

    async def upload(self, paths, css='input[type=file]'):
        """Choose files in the page's file input, as the file dialog would."""
        r = await self.cmd("Runtime.evaluate", expression=f"document.querySelector({json.dumps(css)})")
        oid = r["result"]["objectId"]
        await self.cmd("DOM.enable")
        await self.cmd("DOM.setFileInputFiles", files=[str(Path(p).resolve()) for p in paths], objectId=oid)
        await asyncio.sleep(0.8)

    async def scroll(self, dy, steps=12, css=None):
        target = f"document.querySelector({json.dumps(css)})" if css else "window"
        for _ in range(steps):
            await self.js(f"{target}.scrollBy(0, {dy / steps})")
            await asyncio.sleep(0.04)
        await asyncio.sleep(0.4)

    # ------------------------------------------------------------------ finish
    async def stop(self):
        await asyncio.sleep(1.0)
        self.end_caption()
        try:
            await self.cmd("Page.stopScreencast")
        except Exception:
            pass
        await self.ws.close()
        self.kill()

    def kill(self):
        """Close the browser with all its child processes (terminate() alone leaves Edge running)."""
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)

    def _ass(self):
        def ts(t):
            t = max(0.0, t)
            return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"
        esc = lambda s: s.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")
        head = """[Script Info]
ScriptType: v4.00+
PlayResX: 1440
PlayResY: 900
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Segoe UI,25,&H00FFFFFF,&H00FFFFFF,&H00C26900,&H00C26900,0,0,0,0,100,100,0,0,3,12,0,1,40,40,34,1
Style: Note,Segoe UI,22,&H00FFFFFF,&H00FFFFFF,&H000C0C0C,&H000C0C0C,1,0,0,0,100,100,0,0,3,10,0,9,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        lines = []
        for a, b, title, text in self.captions:
            b = b if b is not None else a + 4
            body = "{\\b1}" + esc(title) + "{\\b0}" + ("\\N" + esc(text) if text else "")
            lines.append(f"Dialogue: 0,{ts(a)},{ts(b)},Cap,,0,0,0,,{body}")
        for a, b, text in self.notes:
            lines.append(f"Dialogue: 1,{ts(a)},{ts(b)},Note,,0,0,0,,{esc(text)}")
        return head + "\n".join(lines) + "\n"

    def encode(self, ffmpeg, fps=30):
        if not self.frames:
            raise RuntimeError("no frames recorded")
        total = self.now() + 0.5
        lst = []
        for (t, f), nxt in zip(self.frames, self.frames[1:] + [(total, None)]):
            lst.append(f"file '{f.name}'\nduration {max(0.001, nxt[0] - t):.3f}")
        lst.append(f"file '{self.frames[-1][1].name}'")
        (self.tmp / "frames.txt").write_text("\n".join(lst) + "\n", encoding="utf-8")
        (self.tmp / "captions.ass").write_text(self._ass(), encoding="utf-8")
        self.out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "frames.txt",
               "-vf", f"fps={fps},scale={W}:{H}:flags=lanczos,format=yuv420p,subtitles=captions.ass",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", str(self.out.resolve())]
        subprocess.run(cmd, cwd=self.tmp, check=True)
        return self.out

    def cleanup(self):
        self.kill()
        time.sleep(1.0)
        shutil.rmtree(self.tmp, ignore_errors=True)
