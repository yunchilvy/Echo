# -*- coding: utf-8 -*-
"""tools/check_ui.py —— 无头渲染界面并做结构断言（不靠肉眼看图）

用法：python tools/check_ui.py [full|mini|settings|all]

检查项：
  * 没有横向溢出，每行内容都在可视宽度内
  * 电量数字的档位配色：>30 绿 / 10–30 橙 / <10 红 / 无读数灰
  * 紧凑模式里电量数字仍然可见且在视口内
  * 低电量预警只对 <10% 的设备弹一次
  * 八向缩放手柄都在
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

DEVICES = [
    {"key": "a", "name": "MOONDROP Pudding", "kind": "earbuds", "level": 86,
     "last_level": 86, "charging": False, "connected": True, "link": "bluetooth",
     "vid_pid": "F6:3F:63", "age": 12, "error": "", "extra": {}},
    {"key": "b", "name": "EDIFIER M230", "kind": "speaker", "level": 30,
     "last_level": 30, "charging": True, "connected": True, "link": "bluetooth",
     "vid_pid": "64:68:76", "age": 34, "error": "", "extra": {}},
    {"key": "c", "name": "Inphic KP 8K 超长名字测试截断", "kind": "combo", "level": 11,
     "last_level": 11, "charging": False, "connected": True, "link": "2.4g",
     "vid_pid": "1D57:FA65", "age": 3, "error": "", "extra": {"asleep": False}},
    {"key": "d", "name": "IN X", "kind": "gamepad", "level": 5,
     "last_level": 5, "charging": False, "connected": True, "link": "2.4g",
     "vid_pid": "1D57:FA65", "age": 5, "error": "", "extra": {"asleep": False}},
    {"key": "e", "name": "Razer BlackShark", "kind": "headset", "level": None,
     "last_level": 74, "charging": False, "connected": False, "link": "bluetooth",
     "vid_pid": "AA:BB:CC", "age": 900, "error": "未连接", "extra": {}},
]

STATE = {"devices": DEVICES, "at": 1793000000,
         "settings": {"interval": 60, "theme": "dark"},
         "autostart": True, "version": "1.2.0", "platform": "Windows"}

MOCK = ("window.pywebview={api:{state:async()=>(" + json.dumps(STATE) + "),"
        "refresh:async()=>(" + json.dumps(STATE) + "),"
        "set_name:async()=>(" + json.dumps(STATE) + "),"
        "set_icon:async()=>(" + json.dumps(STATE) + "),"
        "reset_device:async()=>(" + json.dumps(STATE) + "),"
        "set_setting:async()=>(" + json.dumps(STATE) + "),"
        "set_mode:async()=>(" + json.dumps(STATE) + "),"
        "probe:async()=>({}),brief:async()=>'#',sniff:async()=>({}),"
        "hide_window(){},save_geom(){},start_resize(){},start_drag(){},"
        "quit_app(){},open_data_dir(){},copy_text(){}}};")

PROBE = r"""
(function(){
  const R = (n) => Math.round(n);
  const rows = [...document.querySelectorAll('.row')];
  const vw = document.documentElement.clientWidth;
  const out = {
    viewport: [vw, document.documentElement.clientHeight],
    overflowX: document.documentElement.scrollWidth - vw,
    rows: rows.map(li => {
      const num = li.querySelector('.num');
      const val = li.querySelector('.val');
      const r = val.getBoundingClientRect();
      const name = li.querySelector('.name').getBoundingClientRect();
      return {
        key: li.dataset.key,
        text: (num.textContent || '').trim(),
        color: getComputedStyle(num).color,
        band: val.dataset.band,
        right: R(r.right), w: R(r.width), h: R(r.height),
        nameRight: R(name.right),
        visible: r.width > 6 && r.height > 6
      };
    }),
    handles: document.querySelectorAll('.rz').length,
    alertOn: document.querySelector('#alert').classList.contains('on'),
    alertText: document.querySelector('#alert-text').textContent,
    mini: document.body.classList.contains('mini'),
    cnt: document.querySelector('#cnt').textContent
  };
  document.title = 'PROBE' + JSON.stringify(out);
})();
"""


def render(name, w, h, js, wait=2600):
    src = open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8").read()
    inject = ("<style>.row{opacity:1!important;transform:none!important}"
              + "html,body{width:%dpx!important;height:%dpx!important}" % (w, h)
              + "*{animation:none!important;transition:none!important}</style>"
              "<script>" + MOCK + "</script><script>window.addEventListener('load',()=>{"
              + js + "});</script>")
    html = src.replace("<script>\n/* ===", inject + "<script>\n/* ===", 1)
    path = os.path.join(HERE, f"_check_{name}.html")
    open(path, "w", encoding="utf-8").write(html)
    r = subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--dump-dom",
                        "--virtual-time-budget=%d" % wait,
                        "file:///" + path.replace("\\", "/")],
                       capture_output=True, text=True, encoding="utf-8", errors="ignore")
    m = re.search(r"<title>(.*?)</title>", r.stdout or "", re.S)
    body = m.group(1) if m else ""
    if "PROBE" not in body:
        return None
    return json.loads(body.split("PROBE", 1)[1])


def check(cond, label, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + label + ("" if cond else "   " + detail))
    return cond


def run_full():
    print("[完整视图 640x624]")
    d = render("full", 640, 624, PROBE + "setTimeout(()=>{" + PROBE + "},240);")
    if not d:
        return check(False, "页面没有渲染出来")
    ok = True
    ok &= check(d["overflowX"] <= 0, "无横向溢出", f"多出 {d['overflowX']}px")
    ok &= check(d["handles"] == 8, "八个缩放手柄齐全", str(d["handles"]))
    ok &= check(d["mini"] is False, "默认不是紧凑模式")
    for r in d["rows"]:
        ok &= check(r["visible"] and r["right"] <= d["viewport"][0],
                    f"{r['key']} 电量可见且不越界",
                    f"right={r['right']} vw={d['viewport'][0]}")
    want = {"a": ("86", "ok", "rgb(111, 202, 159)"),
            "b": ("30", "warn", "rgb(229, 179, 106)"),
            "c": ("11", "warn", "rgb(229, 179, 106)"),
            "d": ("5", "low", "rgb(232, 112, 95)"),
            "e": ("—", "none", "rgb(145, 141, 134)")}
    for r in d["rows"]:
        t, band, color = want.get(r["key"], ("", "", ""))
        ok &= check(r["text"] == t and r["band"] == band and r["color"] == color,
                    f"{r['key']} 档位 {band} 与配色", f"{r['text']} {r['band']} {r['color']}")
    ok &= check(d["alertOn"] and "5%" in d["alertText"], "低电量预警弹出且只含 5% 那台",
                d["alertText"])
    ok &= check("86%" not in d["alertText"] and "11%" not in d["alertText"],
                "预警不误伤 11% 与 86%")
    return ok


def run_mini():
    print("[紧凑模式 332x250]")
    d = render("mini", 332, 250, "document.body.classList.add('mini');" + PROBE)
    if not d:
        return check(False, "页面没有渲染出来")
    ok = True
    ok &= check(d["mini"], "紧凑模式生效")
    ok &= check(d["overflowX"] <= 0, "无横向溢出", f"多出 {d['overflowX']}px")
    for r in d["rows"]:
        ok &= check(r["visible"] and r["right"] <= d["viewport"][0] - 4,
                    f"{r['key']} 电量在视口内", f"right={r['right']} vw={d['viewport'][0]}")
    return ok


def run_narrow():
    print("[极窄窗口 320x560]")
    d = render("narrow", 320, 560, PROBE)
    if not d:
        return check(False, "页面没有渲染出来")
    ok = check(d["overflowX"] <= 0, "无横向溢出", f"多出 {d['overflowX']}px")
    for r in d["rows"]:
        ok &= check(r["visible"] and r["right"] <= d["viewport"][0],
                    f"{r['key']} 电量仍在视口内", f"right={r['right']} vw={d['viewport'][0]}")
    return ok


SHEET_PROBE = r"""
(function(){
  const seg = document.getElementById('seg');
  const btns = seg ? [...seg.querySelectorAll('button')] : [];
  const box  = seg ? seg.getBoundingClientRect() : null;
  // 每个分段的字是不是单行、有没有跑出胶囊框
  const segs = btns.map((b) => {
    const r = b.getBoundingClientRect();
    return {text: b.textContent.trim(), h: Math.round(r.height),
            oneLine: r.height <= 26, inside: !!box && r.right <= box.right + 0.5};
  });
  // 说明文字有没有溢出所在条目
  const bad = [...document.querySelectorAll('.item')].filter((it) => {
    const r = it.getBoundingClientRect();
    return [...it.querySelectorAll('.t, .d, code')].some((n) => {
      const q = n.getBoundingClientRect();
      return q.right > r.right + 1 || q.left < r.left - 1;
    });
  }).length;
  document.title = 'PROBE' + JSON.stringify({
    segs, bad, overflowX: document.documentElement.scrollWidth
                                  - document.documentElement.clientWidth
  });
})();
"""


def run_settings():
    ok = True
    for w in (640, 520):
        print(f"[设置面板 {w}x624]")
        js = "openSheet('settings');setTimeout(()=>{" + SHEET_PROBE + "},220);"
        d = render(f"sheet{w}", w, 624, js, 2200)
        if not d:
            ok &= check(False, "页面没有渲染出来")
            continue
        ok &= check(d["overflowX"] <= 0, "无横向溢出", str(d["overflowX"]))
        ok &= check(d["bad"] == 0, "说明文字没跑出边框", f'{d["bad"]} 条越界')
        ok &= check(len(d["segs"]) == 4, "刷新间隔四个选项都在", str(len(d["segs"])))
        for b in d["segs"]:
            ok &= check(b["oneLine"], f'「{b["text"]}」单行', f'高 {b["h"]}px')
            ok &= check(b["inside"], f'「{b["text"]}」在胶囊框内')
    return ok


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    jobs = {"full": run_full, "mini": run_mini, "narrow": run_narrow,
            "settings": run_settings}
    results = [f() for k, f in jobs.items() if which in ("all", k)]
    print()
    print("全部通过" if all(results) else "有失败项")
    sys.exit(0 if all(results) else 1)
