"""带浏览器指纹 + gzip + 限速 + 重试的极简 HTTP 客户端（stdlib urllib，无第三方依赖）。

指纹 header 沿用 2026-07-22 机房实测有效的那套（UA/Accept-Language/sec-ch-ua/Sec-Fetch）。
限速：全局最小请求间隔，礼貌爬，别把源惹毛。HTTPError（业务级状态码）直接返回、不重试；
网络级异常（URLError/超时）退避重试。
"""

import gzip
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "sec-ch-ua": '"Chromium";v="125", "Google Chrome";v="125", "Not.A/Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


class Client:
    def __init__(self, min_interval=1.2, timeout=20, retries=2):
        self.min_interval = min_interval
        self.timeout = timeout
        self.retries = retries
        self._last = 0.0
        # 应用宝 CDN 在部分容器出口偶发证书链问题；本客户端只抓公开只读页、不发凭据，
        # 关闭校验换取稳定（与 sandbox 实测口径一致）。
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    def _throttle(self):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def get(self, url):
        """返回 (status, text)。status 为 int(HTTP 码) 或 "ERR:xxx"（网络级失败重试耗尽）。"""
        last_err = None
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                req = Request(url, headers=_HEADERS)
                with urlopen(req, timeout=self.timeout, context=self._ctx) as resp:
                    raw = resp.read()
                    if "gzip" in (resp.headers.get("Content-Encoding") or ""):
                        raw = gzip.decompress(raw)
                    return resp.status, raw.decode("utf-8", "replace")
            except HTTPError as e:
                return e.code, ""  # 业务级状态码，交调用方判断，不重试
            except (URLError, TimeoutError) as e:
                last_err = e
                time.sleep(1.0 * (attempt + 1))
        return f"ERR:{type(last_err).__name__ if last_err else 'Unknown'}", ""
