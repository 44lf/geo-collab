import argparse
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from server.app.core.paths import ensure_data_dirs, get_data_dir

TOUTIAO_HOME = "https://mp.toutiao.com"
LOGIN_HINTS = ("login", "passport", "sso", "验证码", "扫码", "登录")


def build_state_dir(account_key: str) -> Path:
    return get_data_dir() / "browser_states" / "toutiao" / account_key


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
    parser = argparse.ArgumentParser(description="Toutiao login state spike")
    parser.add_argument("--account-key", default="spike", help="Local account state folder name")
    parser.add_argument(
        "--channel",
        choices=["chromium", "chrome", "msedge"],
        default="msedge",
        help="Browser channel. msedge uses the installed Microsoft Edge when available.",
    )
    parser.add_argument("--executable-path", help="Explicit Chrome/Edge executable path")
    parser.add_argument("--check-only", action="store_true", help="Open saved profile, print status, then close")
    parser.add_argument("--wait-ms", type=int, default=5000, help="Wait time before checking the loaded page")
    args = parser.parse_args()

    ensure_data_dirs()
    state_dir = build_state_dir(args.account_key)
    profile_dir = state_dir / "profile"
    state_path = state_dir / "storage_state.json"
    state_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        if args.check_only:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                **launch_options(args),
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(TOUTIAO_HOME, wait_until="domcontentloaded")
            page.wait_for_timeout(args.wait_ms)
            title = page.title()
            url = page.url
            content = page.locator("body").inner_text(timeout=3000)
            maybe_login = any(hint in f"{url}\n{title}\n{content}" for hint in LOGIN_HINTS)
            context.storage_state(path=str(state_path))
            context.close()
            print(f"url={url}")
            print(f"title={title}")
            print(f"storage_state={state_path}")
            print(f"login_state={'unknown_or_login_page' if maybe_login else 'likely_logged_in'}")
            return

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            **launch_options(args),
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(TOUTIAO_HOME, wait_until="domcontentloaded")

        input("Complete Toutiao login in the browser, then press Enter here...")
        context.storage_state(path=str(state_path))
        context.close()

        verify_context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            **launch_options(args),
        )
        verify_page = verify_context.pages[0] if verify_context.pages else verify_context.new_page()
        verify_page.goto(TOUTIAO_HOME, wait_until="domcontentloaded")
        input("Check whether the reused state is logged in, then press Enter to close...")
        verify_context.close()

    print(f"Saved storage state: {state_path}")
    print(f"Saved persistent profile: {profile_dir}")


if __name__ == "__main__":
    main()
