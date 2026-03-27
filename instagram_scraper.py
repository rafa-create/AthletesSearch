import json
import random
import re
import time

import requests

try:
    from fake_useragent import UserAgent
except Exception:
    UserAgent = None


class InstagramScraper:
    """
    Best-effort Instagram public scraper (no login).

    - Caches results on disk to avoid re-fetching the same profile.
    - Enforces a global rate-limit (>= 15s between requests).
    - Uses simple HTML regex parsing; optionally falls back to Selenium page_source.
    - Never throws to callers by default (returns null fields on failure).
    """

    FOLLOWERS_RE = re.compile(r'"edge_followed_by"\s*:\s*\{"count":\s*(\d+)', re.IGNORECASE)
    POSTS_RE = re.compile(r'"edge_owner_to_timeline_media"\s*:\s*\{"count":\s*(\d+)', re.IGNORECASE)
    BIO_RE = re.compile(r'"biography"\s*:\s*"((?:\\.|[^"\\])*)"', re.IGNORECASE)
    META_DESC_RE = re.compile(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
    )

    def __init__(
        self,
        cache_path: str,
        allow_selenium_fallback: bool = True,
        cache_ttl_days: int = 7,
        logger_fn=None,
    ) -> None:
        self.cache_path = cache_path
        self.allow_selenium_fallback = allow_selenium_fallback
        self.cache_ttl_days = max(1, int(cache_ttl_days))
        self._logger_fn = logger_fn
        self._ua = UserAgent() if UserAgent is not None else None
        self._cache = {}
        self._last_request_ts = 0.0
        self._burst_count = 0
        self._load_cache()

    def _load_cache(self) -> None:
        try:
            with open(self.cache_path, "r", encoding="utf-8") as f:
                self._cache = json.load(f) or {}
        except Exception:
            self._cache = {}

    def _save_cache(self) -> None:
        try:
            with open(self.cache_path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        if callable(self._logger_fn):
            try:
                self._logger_fn(msg)
            except Exception:
                pass

    def _random_ua(self) -> str:
        if self._ua is not None:
            try:
                return self._ua.random
            except Exception:
                pass
        return (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        )

    def _sleep_jitter(self, min_s: float, max_s: float) -> None:
        time.sleep(random.uniform(float(min_s), float(max_s)))

    def _throttle(self) -> None:
        # Global rule: max 1 request every 15 seconds.
        now = time.time()
        elapsed = now - self._last_request_ts
        if elapsed < 15.0:
            time.sleep(15.0 - elapsed)

        # Burst rule: after 20 profiles, pause 2-5 minutes.
        if self._burst_count >= 20:
            self._sleep_jitter(120, 300)
            self._burst_count = 0

    def _http_get_profile_html(self, username: str) -> str:
        self._throttle()
        url = f"https://www.instagram.com/{username.strip().lstrip('@')}/"
        headers = {
            "User-Agent": self._random_ua(),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Referer": "https://www.google.com/",
        }
        r = requests.get(url, headers=headers, timeout=25)
        self._last_request_ts = time.time()
        self._burst_count += 1
        self._log(f"[IG] @{username} HTTP {r.status_code} html_len={len(r.text or '')}")
        # Do not raise for common blocking statuses; just return empty.
        if r.status_code in (401, 403, 429):
            self._log(f"[IG] @{username} HTTP {r.status_code} (public blocked/limited)")
            return ""
        if r.status_code >= 400:
            self._log(f"[IG] @{username} HTTP {r.status_code}")
            return ""
        return r.text or ""

    def _looks_blocked(self, html_text: str) -> bool:
        if not html_text:
            return True
        t = html_text.lower()
        return (
            "challenge_required" in t
            or "login_required" in t
            or "consent_required" in t
            or "not-logged-in" in t
            or "verify" in t and "account" in t
        )

    def _parse_from_html(self, username: str, html_text: str) -> dict:
        out = {"username": username, "followers": None, "posts": None, "bio": None}
        if not html_text:
            self._log(f"[IG] @{username} parse skip: empty html")
            return out

        m = self.FOLLOWERS_RE.search(html_text)
        if m:
            try:
                out["followers"] = int(m.group(1))
            except Exception:
                out["followers"] = None

        m = self.POSTS_RE.search(html_text)
        if m:
            try:
                out["posts"] = int(m.group(1))
            except Exception:
                out["posts"] = None

        m = self.BIO_RE.search(html_text)
        if m:
            try:
                # biography is JSON-escaped; decode minimal escapes.
                raw = m.group(1)
                out["bio"] = json.loads(f'"{raw}"') if raw is not None else None
            except Exception:
                out["bio"] = None

        # Meta description fallback (often contains followers/posts)
        meta_found = False
        if out["followers"] is None or out["posts"] is None:
            m = self.META_DESC_RE.search(html_text)
            if m:
                meta_found = True
                desc = m.group(1)
                # Example: "19M Followers, 423 Posts - See Instagram photos and videos from ..."
                fm = re.search(r"([\d\.,]+)\s*([kKmM]?)\s+Followers", desc)
                pm = re.search(r"([\d\.,]+)\s*([kKmM]?)\s+Posts", desc)
                if out["followers"] is None and fm:
                    out["followers"] = self._parse_km_int(fm.group(1), fm.group(2))
                if out["posts"] is None and pm:
                    out["posts"] = self._parse_km_int(pm.group(1), pm.group(2))
            else:
                meta_found = False

        blocked_hint = self._looks_blocked(html_text)
        self._log(
            f"[IG] @{username} parse details: blocked_hint={blocked_hint} "
            f"meta_desc={'yes' if meta_found else 'no'} "
            f"followers={'yes' if out['followers'] is not None else 'no'} "
            f"posts={'yes' if out['posts'] is not None else 'no'} "
            f"bio={'yes' if bool(out['bio']) else 'no'}"
        )

        return out

    def _parse_km_int(self, num_str: str, suffix: str) -> int | None:
        try:
            n = float(str(num_str).replace(",", "."))
            s = (suffix or "").lower()
            if s == "k":
                return int(n * 1000)
            if s == "m":
                return int(n * 1_000_000)
            return int(n)
        except Exception:
            return None

    def _selenium_page_source(self, username: str) -> str:
        # Optional fallback, imported lazily to keep module lightweight.
        try:
            from selenium import webdriver  # type: ignore
        except Exception:
            return ""

        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("user-agent=" + self._random_ua())
            driver = webdriver.Chrome(options=options)
            try:
                driver.get(f"https://www.instagram.com/{username.strip().lstrip('@')}/")
                time.sleep(2.5)
                return driver.page_source or ""
            finally:
                driver.quit()
        except Exception:
            return ""

    def get_profile(self, username: str) -> dict:
        u = (username or "").strip().lstrip("@")
        if not u:
            return {"username": "", "followers": None, "posts": None, "bio": None}

        # Cache with TTL to keep data relatively fresh.
        cached = self._cache.get(u)
        if isinstance(cached, dict):
            cached_has_data = bool(
                cached.get("bio")
                or cached.get("followers") is not None
                or cached.get("posts") is not None
            )
            updated = cached.get("updated_at")
            if isinstance(updated, (int, float)):
                age_s = time.time() - float(updated)
                if age_s <= self.cache_ttl_days * 86400:
                    if cached_has_data:
                        self._log(f"[IG] @{u} cache hit (age={int(age_s)}s)")
                    else:
                        self._log(f"[IG] @{u} cache hit (empty, age={int(age_s)}s)")
                    return {
                        "username": cached.get("username") or u,
                        "followers": cached.get("followers"),
                        "posts": cached.get("posts"),
                        "bio": cached.get("bio"),
                    }
                if not cached_has_data:
                    self._log(f"[IG] @{u} cache empty expired (age={int(age_s)}s), refresh")
            # legacy entries without timestamp: keep them for 1 day then refresh
            if "updated_at" not in cached:
                if cached_has_data:
                    self._log(f"[IG] @{u} cache hit (legacy)")
                else:
                    self._log(f"[IG] @{u} cache hit (legacy-empty)")
                return {
                    "username": cached.get("username") or u,
                    "followers": cached.get("followers"),
                    "posts": cached.get("posts"),
                    "bio": cached.get("bio"),
                }

        # Anti-block: wait between 8 and 20 seconds between profiles.
        self._sleep_jitter(8, 20)
        self._log(f"[IG] @{u} fetch start")

        last = None
        for attempt in range(1, 4):
            self._log(f"[IG] @{u} attempt {attempt}/3")
            html_text = self._http_get_profile_html(u)
            if html_text and not self._looks_blocked(html_text):
                last = html_text
                break
            # If HTTP blocked/empty, wait 10-30s and retry.
            self._log(f"[IG] @{u} empty/blocked HTML, retrying")
            self._sleep_jitter(10, 30)

        if (not last or self._looks_blocked(last)) and self.allow_selenium_fallback:
            self._log(f"[IG] @{u} fallback selenium")
            src = self._selenium_page_source(u)
            if src and not self._looks_blocked(src):
                last = src
                self._log(f"[IG] @{u} selenium page_source usable len={len(src)}")
            else:
                self._log(f"[IG] @{u} selenium fallback unusable")

        if not last or self._looks_blocked(last):
            data = {"username": u, "followers": None, "posts": None, "bio": None}
            self._log(f"[IG] @{u} no data")
        else:
            data = self._parse_from_html(u, last)
            self._log(
                f"[IG] @{u} parsed followers={data.get('followers')} posts={data.get('posts')} bio={'yes' if data.get('bio') else 'no'}"
            )

        self._cache[u] = {
            "username": data.get("username") or u,
            "followers": data.get("followers"),
            "posts": data.get("posts"),
            "bio": data.get("bio"),
            "updated_at": time.time(),
        }
        self._save_cache()
        return data

    def probe(self) -> bool:
        """
        Quick startup probe. Returns True if public scrape seems to work.
        """
        try:
            data = self.get_profile("instagram")
            return bool(data.get("followers") or data.get("posts") or data.get("bio"))
        except Exception:
            return False

