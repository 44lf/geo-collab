import argparse
import json
from pathlib import Path
from typing import Any

from playwright.sync_api import Locator, Page, sync_playwright

from server.app.core.paths import get_data_dir

TOUTIAO_PUBLISH_URL = "https://mp.toutiao.com/profile_v4/graphic/publish"
LOGIN_HINTS = ("login", "passport", "sso", "验证码", "扫码", "登录")
PUBLISH_HINTS = ("发布", "标题", "正文", "图文", "文章")


def visible_count(locator: Locator) -> int:
    count = locator.count()
    visible = 0
    for index in range(count):
        try:
            if locator.nth(index).is_visible():
                visible += 1
        except Exception:
            continue
    return visible


def collect_candidates(page: Page) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    locator = page.locator("input, textarea, [contenteditable='true']")
    for index in range(locator.count()):
        item = locator.nth(index)
        try:
            if not item.is_visible():
                continue
            fields.append(
                {
                    "index": index,
                    "tag": item.evaluate("node => node.tagName.toLowerCase()"),
                    "type": item.get_attribute("type"),
                    "placeholder": item.get_attribute("placeholder"),
                    "aria": item.get_attribute("aria-label"),
                    "contenteditable": item.get_attribute("contenteditable"),
                    "text": item.inner_text(timeout=1000)[:80],
                    "box": item.bounding_box(),
                }
            )
        except Exception as exc:
            fields.append({"index": index, "error": str(exc)})

    return {
        "url": page.url,
        "title": page.title(),
        "visible_inputs": visible_count(page.locator("input")),
        "visible_textareas": visible_count(page.locator("textarea")),
        "visible_contenteditables": visible_count(page.locator("[contenteditable='true']")),
        "file_inputs": page.locator("input[type='file']").count(),
        "fields": fields[:20],
    }


def try_fill_title(page: Page, title: str) -> bool:
    candidates = [
        "textarea[placeholder*='标题']",
        "input[placeholder*='标题']",
        "[contenteditable='true'][data-placeholder*='标题']",
    ]
    for selector in candidates:
        field = page.locator(selector).first
        try:
            if field.count() and field.is_visible():
                field.click()
                field.fill(title)
                return True
        except Exception:
            continue
    return False


def try_fill_body(page: Page, body: str) -> bool:
    editable = page.locator("[contenteditable='true']")
    for index in range(editable.count()):
        field = editable.nth(index)
        try:
            box = field.bounding_box()
            if not field.is_visible() or not box or box["height"] < 80:
                continue
            field.click()
            page.keyboard.type(body)
            return True
        except Exception:
            continue
    return False


def build_state_path(account_key: str) -> Path:
    return get_data_dir() / "browser_states" / "toutiao" / account_key / "storage_state.json"


def build_profile_dir(account_key: str) -> Path:
    return get_data_dir() / "browser_states" / "toutiao" / account_key / "profile"


def launch_options(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {
        "headless": False,
        "viewport": {"width": 1440, "height": 900},
    }
    if args.channel:
        options["channel"] = args.channel
    if args.executable_path:
        options["executable_path"] = args.executable_path
    return options


def main() -> None:
    parser = argparse.ArgumentParser(description="Toutiao publish page fill spike")
    parser.add_argument("--account-key", default="spike", help="Local account state folder name")
    parser.add_argument("--title", default="Geo 协作平台自动化测试标题")
    parser.add_argument("--body", default="这是一段用于验证头条号发布页自动填充的测试正文。")
    parser.add_argument(
        "--channel",
        choices=["chromium", "chrome", "msedge"],
        default="msedge",
        help="Browser channel. msedge uses the installed Microsoft Edge when available.",
    )
    parser.add_argument("--executable-path", help="Explicit Chrome/Edge executable path")
    parser.add_argument("--check-only", action="store_true", help="Open publish page, print status, then close")
    parser.add_argument("--inspect", action="store_true", help="Print candidate title/body/upload controls")
    parser.add_argument("--fill-test", action="store_true", help="Try filling title and body, without publishing")
    parser.add_argument("--wait-ms", type=int, default=7000, help="Wait time before checking the loaded page")
    args = parser.parse_args()

    state_path = build_state_path(args.account_key)
    if not state_path.exists():
        raise SystemExit(f"Missing storage state: {state_path}. Run toutiao_login_spike.py first.")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(build_profile_dir(args.account_key)),
            **launch_options(args),
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(TOUTIAO_PUBLISH_URL, wait_until="domcontentloaded")

        if args.check_only:
            page.wait_for_timeout(args.wait_ms)
            title = page.title()
            url = page.url
            content = page.locator("body").inner_text(timeout=3000)
            haystack = f"{url}\n{title}\n{content}"
            maybe_login = any(hint in haystack for hint in LOGIN_HINTS)
            maybe_publish = any(hint in haystack for hint in PUBLISH_HINTS)
            context.close()
            print(f"url={url}")
            print(f"title={title}")
            print(f"publish_state={'likely_publish_page' if maybe_publish and not maybe_login else 'unknown_or_login_page'}")
            return

        if args.inspect or args.fill_test:
            page.wait_for_timeout(args.wait_ms)
            before = collect_candidates(page)
            fill_result = None
            if args.fill_test:
                fill_result = {
                    "title_filled": try_fill_title(page, args.title),
                    "body_filled": try_fill_body(page, args.body),
                }
                page.wait_for_timeout(1500)
            after = collect_candidates(page) if args.fill_test else None
            context.close()
            print(json.dumps({"before": before, "fill_result": fill_result, "after": after}, ensure_ascii=False))
            return

        print("Opened Toutiao publish page.")
        print("Next manual spike steps:")
        print("1. Confirm the final publish URL is correct.")
        print("2. Inspect stable selectors for title, body editor, cover upload.")
        print("3. Do not click the final publish button.")
        print(f"Test title: {args.title}")
        print(f"Test body: {args.body}")
        input("Press Enter to close the browser after recording selectors...")

        context.close()


if __name__ == "__main__":
    main()
