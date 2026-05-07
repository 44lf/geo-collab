"""临时诊断脚本 — 截图 + DOM dump，用完即删"""
from __future__ import annotations
import json, sys
from pathlib import Path

PROFILE = Path(r"C:\Users\Administrator\AppData\Local\GeoCollab\browser_states\toutiao\chrome-spike\profile")
PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
OUT_DIR = Path(r"E:\geo\scripts")

from playwright.sync_api import sync_playwright

pw = sync_playwright().start()
ctx = pw.chromium.launch_persistent_context(
    user_data_dir=str(PROFILE),
    channel="chrome",
    headless=False,
    viewport={"width": 1440, "height": 900},
    args=["--start-maximized"],
)
page = ctx.pages[0] if ctx.pages else ctx.new_page()
page.goto(PUBLISH_URL, wait_until="domcontentloaded")
page.wait_for_timeout(6000)
try:
    page.wait_for_load_state("networkidle", timeout=8000)
except Exception:
    pass

# 截图
shot1 = OUT_DIR / "shot_full.png"
page.screenshot(path=str(shot1), full_page=True)
print("截图:", shot1)

# 滚到底部后再截一张（显示底部按钮区）
page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
page.wait_for_timeout(800)
shot2 = OUT_DIR / "shot_bottom.png"
page.screenshot(path=str(shot2))
print("底部截图:", shot2)

# dump 所有按钮
buttons = page.evaluate("""() => Array.from(document.querySelectorAll(
    'button, [role="button"], .ant-btn, input[type="submit"]'
)).filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
}).map(el => ({
    tag: el.tagName,
    text: (el.textContent || el.value || '').trim().replace(/\\s+/g,' ').slice(0,80),
    cls: el.className.slice(0, 100),
    type: el.type || '',
    disabled: !!el.disabled,
    rect: el.getBoundingClientRect().toJSON(),
}))""")
print(f"\n=== 按钮 ({len(buttons)}) ===")
for b in buttons:
    print(json.dumps(b, ensure_ascii=False))

# dump 编辑器
editors = page.evaluate("""() => Array.from(document.querySelectorAll(
    '[contenteditable], textarea, input:not([type="hidden"]):not([type="file"])'
)).filter(el => {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
}).map(el => ({
    tag: el.tagName,
    type: el.type || '',
    ph: el.getAttribute('placeholder') || el.dataset.placeholder || '',
    editable: el.getAttribute('contenteditable'),
    rect: el.getBoundingClientRect().toJSON(),
}))""")
print(f"\n=== 编辑器 ({len(editors)}) ===")
for e in editors:
    print(json.dumps(e, ensure_ascii=False))

ctx.close()
pw.stop()
print("\nDone.")
