"""
头条号发布页面诊断脚本。

用法：
    python scripts/test_toutiao_publish.py [account_key]

account_key 可省略 — 会自动扫描 data/browser_states/toutiao/ 下第一个账号。
加 --fill 参数会尝试填写测试标题+正文（不点发布）。
加 --publish 参数会填写并尝试点击发布按钮（真实发布，慎用）。

示例：
    python scripts/test_toutiao_publish.py
    python scripts/test_toutiao_publish.py mykey --fill
    python scripts/test_toutiao_publish.py mykey --publish
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 把项目根加入 path，使 server.* 可以 import
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"


def find_account_key(data_dir: Path) -> str | None:
    base = data_dir / "browser_states" / "toutiao"
    if not base.exists():
        return None
    for p in base.iterdir():
        if p.is_dir() and (p / "storage_state.json").exists():
            return p.name
    return None


def dump_buttons(page) -> list[dict]:
    """JS 枚举页面所有可见按钮并打印。"""
    buttons: list[dict] = page.evaluate("""() => {
        return Array.from(document.querySelectorAll(
            'button, [role="button"], .ant-btn, input[type="submit"]'
        ))
        .filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        })
        .map(el => ({
            tag: el.tagName,
            text: (el.textContent || el.value || '').trim().replace(/\\s+/g, ' ').slice(0, 60),
            cls: el.className.slice(0, 80),
            type: el.type || '',
            disabled: !!el.disabled
        }));
    }""")
    print(f"\n=== 可见按钮（共 {len(buttons)} 个）===")
    for i, b in enumerate(buttons):
        flag = " [disabled]" if b["disabled"] else ""
        print(f"  [{i}] <{b['tag']} type={b['type']!r}> text=「{b['text']}」 cls={b['cls']!r}{flag}")
    return buttons


def dump_editable(page) -> None:
    """列出所有可编辑区域。"""
    info: list[dict] = page.evaluate("""() => {
        return Array.from(document.querySelectorAll(
            '[contenteditable="true"], textarea, input[type="text"], input[type="tel"]'
        ))
        .filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0;
        })
        .map(el => ({
            tag: el.tagName,
            type: el.type || '',
            ph: el.getAttribute('placeholder') || el.dataset.placeholder || '',
            w: Math.round(el.getBoundingClientRect().width),
            h: Math.round(el.getBoundingClientRect().height),
        }));
    }""")
    print(f"\n=== 可编辑区域（共 {len(info)} 个）===")
    for i, f in enumerate(info):
        print(f"  [{i}] <{f['tag']} type={f['type']!r}> placeholder={f['ph']!r} size={f['w']}x{f['h']}")


def try_click_publish(page) -> bool:
    """尝试点击发布按钮，返回是否成功点击。"""
    # 先滚到底部
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(600)

    keywords = ["发布", "提交", "Publish", "Submit"]
    clicked: bool = page.evaluate("""(kws) => {
        const els = Array.from(document.querySelectorAll(
            'button, [role="button"], .ant-btn, input[type="submit"]'
        ));
        const byText = els.find(el => {
            const text = (el.textContent || el.value || '').trim();
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && !el.disabled
                && kws.some(k => text.includes(k));
        });
        if (byText) {
            byText.dispatchEvent(new MouseEvent('click', {bubbles: true}));
            return true;
        }
        const primaries = els.filter(el => {
            const r = el.getBoundingClientRect();
            return r.width > 0 && r.height > 0 && !el.disabled
                && (el.classList.contains('ant-btn-primary') || el.type === 'submit');
        });
        if (primaries.length) {
            primaries[primaries.length - 1].dispatchEvent(new MouseEvent('click', {bubbles: true}));
            return true;
        }
        return false;
    }""", keywords)

    if not clicked:
        for sel in [
            "button:has-text('发布')", "button:has-text('立即发布')", "button:has-text('提交发布')",
            "button:has-text('提交')", ".ant-btn-primary", "button[type='submit']",
        ]:
            try:
                loc = page.locator(sel).last
                if loc.count():
                    loc.click(force=True, timeout=3000)
                    clicked = True
                    print(f"  → Playwright locator 点击: {sel}")
                    break
            except Exception:
                continue

    return clicked


def main() -> None:
    parser = argparse.ArgumentParser(description="头条号发布页诊断工具")
    parser.add_argument("account_key", nargs="?", help="账号 key（可选，默认自动查找）")
    parser.add_argument("--fill", action="store_true", help="填写测试标题和正文（不点发布）")
    parser.add_argument("--publish", action="store_true", help="填写并点击发布按钮（真实发布）")
    parser.add_argument("--wait", type=int, default=8000, help="页面加载等待毫秒（默认 8000）")
    args = parser.parse_args()

    from server.app.core.paths import get_data_dir
    from server.app.services.accounts import profile_dir_for_key

    data_dir = get_data_dir()
    account_key = args.account_key or find_account_key(data_dir)
    if not account_key:
        print("未找到任何账号，请先在应用里登录头条号", file=sys.stderr)
        sys.exit(1)

    state_path = data_dir / "browser_states" / "toutiao" / account_key / "storage_state.json"
    profile_dir = profile_dir_for_key(account_key)

    print(f"账号 key : {account_key}")
    print(f"state   : {state_path}")
    print(f"profile : {profile_dir}")
    print(f"目标 URL : {PUBLISH_URL}")
    print("=" * 60)

    from playwright.sync_api import sync_playwright

    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=False,
        viewport={"width": 1440, "height": 900},
    )
    page = context.pages[0] if context.pages else context.new_page()

    try:
        print(f"正在打开 {PUBLISH_URL} ...")
        page.goto(PUBLISH_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(args.wait)

        print(f"\n当前 URL  : {page.url}")
        print(f"页面标题  : {page.title()}")

        # 等待网络静止
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
            print("网络已静止")
        except Exception:
            print("网络静止超时，继续...")

        dump_editable(page)
        dump_buttons(page)

        if args.fill or args.publish:
            print("\n=== 填写表单 ===")
            # 填标题
            title_filled = False
            for sel in [
                "textarea[placeholder*='标题']",
                "textarea",
                "input[placeholder*='标题']",
                "[contenteditable='true'][data-placeholder*='标题']",
            ]:
                f = page.locator(sel).first
                try:
                    if f.count() and f.is_visible():
                        f.click()
                        f.fill("测试标题（诊断脚本）")
                        print(f"  标题已填写: {sel}")
                        title_filled = True
                        break
                except Exception:
                    continue
            if not title_filled:
                print("  标题字段未找到")

            # 填正文
            body_filled = False
            editable = page.locator("[contenteditable='true']")
            for i in range(editable.count()):
                field = editable.nth(i)
                try:
                    box = field.bounding_box()
                    if not field.is_visible() or not box or box["height"] < 80:
                        continue
                    field.click()
                    page.keyboard.type("这是诊断脚本填写的测试正文，不会真正发布。")
                    print(f"  正文已填写 (index={i})")
                    body_filled = True
                    break
                except Exception:
                    continue
            if not body_filled:
                print("  正文编辑区未找到")

            page.wait_for_timeout(1000)
            # 再次枚举按钮（填写后可能有变化）
            print("\n=== 填写后重新枚举按钮 ===")
            dump_buttons(page)

        if args.publish:
            print("\n=== 尝试点击发布按钮 ===")
            clicked = try_click_publish(page)
            if clicked:
                print("  发布按钮已点击，等待页面跳转（30s）...")
                before = page.url
                try:
                    page.wait_for_url(lambda url: url != before, timeout=30000)
                    print(f"  发布成功！新 URL: {page.url}")
                except Exception:
                    body = page.locator("body").inner_text(timeout=3000)
                    print(f"  URL 未跳转，页面文本片段: {body[:400]}")
            else:
                print("  发布按钮未找到（见上方按钮列表）")

        if not args.publish:
            print("\n浏览器保持打开，按 Enter 关闭...")
            input()

    finally:
        context.close()
        playwright.stop()


if __name__ == "__main__":
    main()
