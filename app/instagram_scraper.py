import json
import random
import re
import time
import datetime as dt

import requests

try:
    from fake_useragent import UserAgent
except Exception:
    UserAgent = None

try:
    from instagrapi import Client as IGClient
except Exception:
    IGClient = None


class InstagramScraper:
    """
    Best-effort Instagram scraper.

    - Pas de cache disque : chaque get_profile refait un appel (tests et diagnostic fiables).
    - Rate-limit global (intervalle entre requêtes).
    - Parse HTML par regex ; repli Selenium optionnel sur page_source.
    """

    FOLLOWERS_RE = re.compile(r'"edge_followed_by"\s*:\s*\{"count":\s*(\d+)', re.IGNORECASE)
    POSTS_RE = re.compile(r'"edge_owner_to_timeline_media"\s*:\s*\{"count":\s*(\d+)', re.IGNORECASE)
    BIO_RE = re.compile(r'"biography"\s*:\s*"((?:\\.|[^"\\])*)"', re.IGNORECASE)
    META_DESC_RE = re.compile(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', re.IGNORECASE
    )

    def __init__(
        self,
        allow_selenium_fallback: bool = True,
        logger_fn=None,
        auth_username: str = "",
        auth_password: str = "",
    ) -> None:
        self.allow_selenium_fallback = allow_selenium_fallback
        self._logger_fn = logger_fn
        self._ua = UserAgent() if UserAgent is not None else None
        self._last_request_ts = 0.0
        self._burst_count = 0
        self._degraded_until_by_user = {}
        self._last_json_status_by_user = {}
        self._fast_mode = False
        self._auth_username = (auth_username or "").strip()
        self._auth_password = (auth_password or "").strip()
        self._ig_client = None
        self._ig_logged_in = False

    def set_credentials(self, username: str, password: str) -> None:
        self._auth_username = (username or "").strip()
        self._auth_password = (password or "").strip()
        self._ig_client = None
        self._ig_logged_in = False

    def set_fast_mode(self, enabled: bool) -> None:
        self._fast_mode = bool(enabled)

    def _log(self, msg: str) -> None:
        if callable(self._logger_fn):
            try:
                self._logger_fn(msg)
            except Exception:
                pass

    def has_auth_credentials(self) -> bool:
        return bool(self._auth_username and self._auth_password and IGClient is not None)

    def _ensure_auth_client(self) -> bool:
        if self._ig_logged_in and self._ig_client is not None:
            return True
        if not self.has_auth_credentials():
            return False
        try:
            self._log(f"[IG] login auth start @{self._auth_username}")
            client = IGClient()
            client.login(self._auth_username, self._auth_password)
            self._ig_client = client
            self._ig_logged_in = True
            self._log(f"[IG] login auth OK @{self._auth_username}")
            return True
        except Exception as ex:
            self._ig_client = None
            self._ig_logged_in = False
            self._log(f"[IG] login auth KO: {ex}")
            return False

    def _auth_get_profile(self, username: str) -> dict | None:
        if not self._ensure_auth_client():
            return None
        try:
            u = username.strip().lstrip("@")
            user = self._ig_client.user_info_by_username(u)
            followers = getattr(user, "follower_count", None)
            posts = getattr(user, "media_count", None)
            bio = (getattr(user, "biography", "") or "").strip() or None

            posts_last_90d = None
            try:
                cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
                medias = self._ig_client.user_medias(user.pk, amount=50)
                cnt = 0
                for media in medias:
                    taken_at = getattr(media, "taken_at", None)
                    if taken_at is None:
                        continue
                    t = taken_at if taken_at.tzinfo else taken_at.replace(tzinfo=dt.timezone.utc)
                    if t >= cutoff:
                        cnt += 1
                    else:
                        break
                posts_last_90d = cnt
            except Exception:
                posts_last_90d = None

            out = {
                "username": u,
                "followers": int(followers) if isinstance(followers, int) else None,
                "posts": int(posts) if isinstance(posts, int) else None,
                "bio": bio,
                "posts_last_90d": posts_last_90d,
            }
            bio_note = ""
            if bio:
                snippet = bio.replace("\n", " ").strip()
                if len(snippet) > 90:
                    snippet = snippet[:90] + "…"
                bio_note = f" bio_preview={snippet!r}"
            self._log(
                f"[IG] @{u} auth parsed followers={out.get('followers')} "
                f"posts={out.get('posts')} posts_90j={out.get('posts_last_90d')} "
                f"bio={'yes' if out.get('bio') else 'no'}{bio_note}"
            )
            return out
        except Exception as ex:
            self._log(f"[IG] @{username} auth fetch KO: {ex}")
            return None

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
        # Dynamic rate-limit: aggressive in fast mode, conservative otherwise.
        min_interval = 2.0 if self._fast_mode else 15.0
        now = time.time()
        elapsed = now - self._last_request_ts
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        # Burst rule.
        if self._fast_mode and self._burst_count >= 40:
            self._sleep_jitter(15, 30)
            self._burst_count = 0
        elif (not self._fast_mode) and self._burst_count >= 20:
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

    def _http_get_profile_json(self, username: str) -> dict | None:
        """
        Try Instagram web profile info endpoint (more stable than HTML regex in many cases).
        Returns parsed dict or None.
        """
        self._throttle()
        u = username.strip().lstrip("@")
        url = f"https://i.instagram.com/api/v1/users/web_profile_info/?username={u}"
        headers = {
            "User-Agent": self._random_ua(),
            "Accept": "application/json",
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Referer": f"https://www.instagram.com/{u}/",
            # Public web app id commonly used by browser requests.
            "x-ig-app-id": "936619743392459",
        }
        try:
            r = requests.get(url, headers=headers, timeout=20)
            self._last_request_ts = time.time()
            self._burst_count += 1
            self._last_json_status_by_user[u] = int(r.status_code)
            self._log(f"[IG] @{u} JSON HTTP {r.status_code}")
            if r.status_code in (401, 403, 404, 429):
                return None
            if r.status_code >= 400:
                return None
            data = r.json()
            user = ((data or {}).get("data") or {}).get("user") or {}
            if not user:
                return None
            followers = user.get("edge_followed_by", {}).get("count")
            posts = user.get("edge_owner_to_timeline_media", {}).get("count")
            bio = (user.get("biography") or "").strip() or None
            # Posts publiés dans les ~90 derniers jours (fenêtre du fil d’aperçu renvoyé par l’API, souvent ≤12).
            posts_last_90d = None
            try:
                cutoff = int(time.time()) - 90 * 86400
                edges = (user.get("edge_owner_to_timeline_media") or {}).get("edges") or []
                posts_last_90d = sum(
                    1
                    for e in edges
                    if int((e or {}).get("node", {}).get("taken_at_timestamp") or 0) >= cutoff
                )
            except Exception:
                posts_last_90d = None
            return {
                "username": u,
                "followers": int(followers) if isinstance(followers, int) else None,
                "posts": int(posts) if isinstance(posts, int) else None,
                "bio": bio,
                "posts_last_90d": posts_last_90d,
            }
        except Exception:
            return None

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
        out = {"username": username, "followers": None, "posts": None, "bio": None, "posts_last_90d": None}
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

        if out.get("posts_last_90d") is None:
            p90 = self._parse_posts_last_90d_from_instagram_html(html_text)
            if p90 is not None:
                out["posts_last_90d"] = p90
                self._log(f"[IG] @{username} posts_90j from embedded HTML: {p90}")

        return out

    def _parse_posts_last_90d_from_instagram_html(self, html_text: str) -> int | None:
        """
        When the JSON API returns 401, the public HTML may still embed GraphQL-shaped JSON
        with taken_at_timestamp for the preview grid — count posts within ~90 days.
        """
        if not html_text or "edge_owner_to_timeline_media" not in html_text:
            return None
        idx = html_text.find("edge_owner_to_timeline_media")
        chunk = html_text[idx : idx + 650_000]
        cutoff = int(time.time()) - 90 * 86400
        ts: list[int] = []
        for m in re.finditer(r'"taken_at_timestamp"\s*:\s*(\d+)', chunk):
            try:
                ts.append(int(m.group(1)))
            except Exception:
                continue
        if not ts:
            return None
        if len(ts) > 64:
            ts = ts[:64]
        return sum(1 for t in ts if t >= cutoff)

    def _explain_posts_90j_none_from_html(self, html_text: str) -> str:
        """Raison courte (FR) si _parse_posts_last_90d_from_instagram_html aurait renvoyé None."""
        if not html_text:
            return "aucun HTML"
        if "edge_owner_to_timeline_media" not in html_text:
            return "pas de chaîne edge_owner_to_timeline_media dans la page"
        idx = html_text.find("edge_owner_to_timeline_media")
        chunk = html_text[idx : idx + 650_000]
        if not re.search(r'"taken_at_timestamp"\s*:\s*(\d+)', chunk):
            if re.search(r'"taken_at_timestamp"\s*:\s*(\d+)', html_text):
                return "taken_at_timestamp présents ailleurs que dans le segment fil (page fragmentée)"
            return "pas de taken_at_timestamp dans le segment du fil d’aperçu"
        return "comptage 90j impossible (incohérence parse)"

    def _log_posts_90j_none(
        self,
        u: str,
        *,
        json_ok: bool,
        json_data: dict | None,
        last_html: str | None,
        final: dict,
    ) -> None:
        """Log une ligne explicite lorsque posts_last_90d reste None."""
        if final.get("posts_last_90d") is not None:
            return
        st = self._last_json_status_by_user.get(u)
        api = f"HTTP {st}" if st is not None else "n/a"

        if json_ok and json_data is not None:
            self._log(
                f"[IG] @{u} posts_90j=None: JSON reçu mais comptage 90j impossible "
                f"(edges vides, pas de taken_at_timestamp, ou exception dans le parse)"
            )
            return

        parts: list[str] = []
        if st == 401:
            parts.append("API JSON 401 (refus / session requise côté Instagram)")
        elif st in (403, 404, 429):
            parts.append(f"API JSON {st}")
        elif st is not None and st >= 400:
            parts.append(f"API JSON {st}")
        else:
            parts.append(f"API JSON indisponible ou vide ({api})")

        if not last_html:
            parts.append("aucun HTML exploitable après repli")
            self._log(f"[IG] @{u} posts_90j=None: " + " ; ".join(parts))
            return
        if self._looks_blocked(last_html):
            parts.append("HTML interprété comme page bloquée / login")
        else:
            parts.append(self._explain_posts_90j_none_from_html(last_html))
        self._log(f"[IG] @{u} posts_90j=None: " + " ; ".join(parts))

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

    def get_profile(self, username: str, force_refresh: bool = False) -> dict:
        u = (username or "").strip().lstrip("@")
        if not u:
            return {"username": "", "followers": None, "posts": None, "bio": None, "posts_last_90d": None}

        auth_data = self._auth_get_profile(u)
        if auth_data is not None:
            return auth_data

        # Fast-fail window when Instagram public surface is temporarily unusable for this handle.
        now = time.time()
        degraded_until = float(self._degraded_until_by_user.get(u) or 0.0)
        if now < degraded_until:
            remaining = int(degraded_until - now)
            self._log(f"[IG] @{u} skip scrape (degraded mode handle {remaining}s)")
            self._log(f"[IG] @{u} posts_90j=None: scrape ignoré (mode dégradé {remaining}s)")
            return {"username": u, "followers": None, "posts": None, "bio": None, "posts_last_90d": None}

        if force_refresh:
            self._log(f"[IG] @{u} fetch prioritaire (handle manuel / découvert)")

        # Anti-block pacing between profile attempts.
        if self._fast_mode:
            self._sleep_jitter(0.4, 1.2)
        else:
            self._sleep_jitter(8, 20)
        self._log(f"[IG] @{u} fetch start")

        # Step 1: JSON endpoint first
        json_data = self._http_get_profile_json(u)
        if json_data is not None:
            self._log(
                f"[IG] @{u} JSON parsed followers={json_data.get('followers')} "
                f"posts={json_data.get('posts')} posts_90j={json_data.get('posts_last_90d')} "
                f"bio={'yes' if json_data.get('bio') else 'no'}"
            )
            if json_data.get("posts_last_90d") is None:
                self._log_posts_90j_none(
                    u, json_ok=True, json_data=json_data, last_html=None, final=json_data
                )
            return json_data

        # Step 2: HTML fallback
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
            data = {"username": u, "followers": None, "posts": None, "bio": None, "posts_last_90d": None}
            self._log(f"[IG] @{u} no data")
        else:
            data = self._parse_from_html(u, last)
            self._log(
                f"[IG] @{u} parsed followers={data.get('followers')} posts={data.get('posts')} bio={'yes' if data.get('bio') else 'no'}"
            )
            # If HTML parse succeeds technically but yields empty fields, try Selenium once as deep fallback.
            if (
                self.allow_selenium_fallback
                and data.get("followers") is None
                and data.get("posts") is None
                and not data.get("bio")
            ):
                self._log(f"[IG] @{u} html parse empty, try selenium deep fallback")
                src = self._selenium_page_source(u)
                if src and not self._looks_blocked(src):
                    data2 = self._parse_from_html(u, src)
                    if data2.get("followers") is not None or data2.get("posts") is not None or data2.get("bio"):
                        data = data2
                        self._log(
                            f"[IG] @{u} selenium parsed followers={data.get('followers')} posts={data.get('posts')} bio={'yes' if data.get('bio') else 'no'}"
                        )
        # Compact diagnostic line for quick troubleshooting in app logs.
        if (
            data.get("followers") is None
            and data.get("posts") is None
            and not data.get("bio")
        ):
            json_status = self._last_json_status_by_user.get(u)
            html_state = "missing"
            if last:
                html_state = "blocked" if self._looks_blocked(last) else "no-markers"
            self._log(
                f"[IG] @{u} diagnostic: empty data "
                f"(json_http={json_status if json_status is not None else 'n/a'}, html={html_state})"
            )

        # If both JSON and HTML yielded no usable data, pause scraping attempts for a short period.
        if (
            json_data is None
            and data.get("followers") is None
            and data.get("posts") is None
            and not data.get("bio")
        ):
            self._degraded_until_by_user[u] = time.time() + 600  # 10 min per handle
            self._log(f"[IG] @{u} degraded mode enabled for handle (600s)")

        if data.get("posts_last_90d") is None:
            self._log_posts_90j_none(
                u, json_ok=False, json_data=None, last_html=last, final=data
            )

        return data

    def probe(self) -> bool:
        """Test léger au démarrage : page d’accueil Instagram (pas de cache)."""
        try:
            r = requests.get(
                "https://www.instagram.com/",
                headers={
                    "User-Agent": self._random_ua(),
                    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
                    "Referer": "https://www.google.com/",
                },
                timeout=12,
            )
            ok = r.status_code == 200 and len(r.text or "") > 800
            self._log(f"[IG] probe HTTP {r.status_code} (accueil) ok={ok}")
            return bool(ok)
        except Exception as ex:
            self._log(f"[IG] probe échec: {ex}")
            return False

