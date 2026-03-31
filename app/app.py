import csv
import datetime as dt
import os
import shutil
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
import socket
import re
import html
import json
import random
import subprocess
import sys
import platform
import tempfile
import stat
from dataclasses import dataclass
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Dict, List, Optional, Tuple
import webbrowser
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
import requests
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None
try:
    from fake_useragent import UserAgent
except Exception:
    UserAgent = None
try:
    from loguru import logger
except Exception:
    logger = None

try:
    from instagram_scraper import InstagramScraper
except Exception:
    InstagramScraper = None

webdriver = None
WebDriverException = Exception
By = None

class SearchCancelled(Exception):
    pass

APP_NAME = "Athletes Searcher"
APP_VERSION = "v01.00.00"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"
MIN_SPLASH_MS = 1200
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# Root delivery folder (launcher sets ATHLETES_ROOT so CSV/logs go to root).
# Fallback: use parent of ./app when running from sources directly.
ROOT_DIR = os.path.abspath(os.getenv("ATHLETES_ROOT") or os.path.join(APP_DIR, os.pardir))
# Runtime cache/logs inside app folder (keeps delivery root clean)
CACHE_DIR = os.path.join(APP_DIR, ".appdata")
LOG_DIR = os.path.join(CACHE_DIR, "logs")
# Internal app data under .appdata to avoid root pollution.
DATA_DIR = os.path.join(CACHE_DIR, "Data")
PENDING_CSV_PATH = os.path.join(CACHE_DIR, "pending_csv_after_update.txt")
_single_instance_socket = None
def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _resource_path(rel: str) -> str:
    """
    Path helper compatible with PyInstaller (.app/.exe).
    When frozen, resources are unpacked under sys._MEIPASS.
    """
    base = getattr(sys, "_MEIPASS", APP_DIR)
    return os.path.join(base, rel)


def _macos_updater_script_path() -> Optional[str]:
    """Locate MiseAJour_macOS_Et_Relance.sh inside a PyInstaller .app or dev tree."""
    candidates = [
        _resource_path("MiseAJour_macOS_Et_Relance.sh"),
        os.path.join(APP_DIR, "MiseAJour_macOS_Et_Relance.sh"),
        os.path.join(os.path.dirname(os.path.abspath(sys.executable or "")), "MiseAJour_macOS_Et_Relance.sh"),
        os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(sys.executable or "")), "..", "Resources", "MiseAJour_macOS_Et_Relance.sh")
        ),
    ]
    for c in candidates:
        try:
            p = os.path.normpath(os.path.abspath(c))
            if os.path.isfile(p):
                return p
        except Exception:
            continue
    return None


# Instagram auth defaults (override with env IG_USER / IG_PASS).
IG_DEFAULT_USER = "pogo.loc"
IG_DEFAULT_PASS = "Pogo54500/"
DEFAULT_SPORT = "foot"
DEFAULT_VILLE = "paris"
DEFAULT_CLUB = "psg"
CLUB_WIKIDATA_QIDS = {
    "psg": "Q483020",  # Paris Saint-Germain F.C.
    "paris sg": "Q483020",
    "paris-sg": "Q483020",
    "paris saint-germain": "Q483020",
    "paris saint-germain f.c.": "Q483020",
}

# Optionnel : URL d'effectif connue quand Google/DDGS ne trouve pas (club atypique, SPA, etc.).
# Pour la plupart des clubs FR : la découverte « Effectif + nom du club » suffit (pas besoin de tout lister).
OFFICIAL_CLUB_ROSTER_URLS: Dict[str, str] = {}
# Fast mode by default. Set IG_FAST_MODE=0 to force complete mode at startup.
IG_FAST_MODE_DEFAULT = str(os.getenv("IG_FAST_MODE", "1")).strip().lower() not in ("0", "false", "no")

# Sections Wikipédia (fr) : ordre important — les plus « actuel » en premier pour éviter les listes historiques.
WIKI_FR_ROSTER_SECTIONS_STRICT = [
    "Effectif actuel",
    "Effectif professionnel",
    "Effectif",
    "Joueurs",
]
WIKI_FR_ROSTER_SECTIONS_LOOSE = WIKI_FR_ROSTER_SECTIONS_STRICT + ["Liste des joueurs"]

CSV_COLUMNS = [
    "Nom",
    "Prénom",
    "Date d'ajout",
    "Sport",
    "Date de naissance",
    "Age",
    "Club",
    "Ville",
    "Instagram",
    "Nombre d'abonnés",
    "Nombre de posts",
    "Posts 3 derniers mois",
    "Nombre de points",
    "Niveau Barème",
    "Info en bio",
    "Autres informations",
    "Nationalité",
    "Priorité",
]

# External CSV format expected by clients / example file.
CSV_EXPORT_COLUMNS = [
    "Nom",
    "Prénom",
    "Date d'ajout",
    "Sport",
    "Date de naissance",
    "Age",
    "Club",
    "Ville",
    "Instagram",
    "Nombre d'abonnés",
    "Nombre de post",
    "Post 3 derniers mois",
    "Nombre de points",
    "Niveau Barème",
    "Info en bio",
    "Autres informations",
    "Nationalité",
    "Priorité",
]
EXAMPLE_DIR = os.path.join(APP_DIR, "exemple")
XLSX_TEMPLATE_PATH = os.path.join(EXAMPLE_DIR, "template_sportifs.xlsx")

def ensure_app_folders() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(EXAMPLE_DIR, exist_ok=True)


def now_str() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d")


def default_season_label(now: Optional[dt.datetime] = None) -> str:
    """
    Football season label like "2025-2026".
    Uses July as season boundary (>= July => new season starts current year).
    """
    n = now or dt.datetime.now()
    start_year = n.year if n.month >= 7 else (n.year - 1)
    return f"{start_year}-{start_year + 1}"


def parse_season_start_year(season_text: str) -> Optional[int]:
    """
    Accepts: "2025-2026", "2025/2026", "2025 2026", or "2025".
    Returns the season start year as int.
    """
    s = (season_text or "").strip()
    if not s:
        return None
    years = [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", s)]
    if not years:
        return None
    return min(years)


def calc_age(birth_date_str: str) -> str:
    if not birth_date_str:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            born = dt.datetime.strptime(birth_date_str.strip(), fmt).date()
            today = dt.date.today()
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
            return str(age)
        except ValueError:
            continue
    return ""


def _days_in_month(year: int, month: int) -> int:
    # month: 1..12
    if month == 12:
        nxt = dt.date(year + 1, 1, 1)
    else:
        nxt = dt.date(year, month + 1, 1)
    cur = dt.date(year, month, 1)
    return (nxt - cur).days


def _add_months(d: dt.date, months: int) -> dt.date:
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    day = min(d.day, _days_in_month(y, m))
    return dt.date(y, m, day)


def calc_age_ymd(birth_date_str: str, *, today: Optional[dt.date] = None) -> Optional[Tuple[int, int, int]]:
    """Return (years, months, days) between birth date and today."""
    if not birth_date_str:
        return None
    t = today or dt.date.today()
    born = None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            born = dt.datetime.strptime(birth_date_str.strip(), fmt).date()
            break
        except ValueError:
            continue
    if born is None or born > t:
        return None

    years = t.year - born.year
    try:
        anchor = dt.date(born.year + years, born.month, min(born.day, _days_in_month(born.year + years, born.month)))
    except Exception:
        return None
    if anchor > t:
        years -= 1
        anchor = dt.date(born.year + years, born.month, min(born.day, _days_in_month(born.year + years, born.month)))

    months = 0
    while True:
        nxt = _add_months(anchor, months + 1)
        if nxt <= t:
            months += 1
        else:
            break
        if months >= 12:
            break
    anchor2 = _add_months(anchor, months)
    days = (t - anchor2).days
    return years, months, days


def calc_age_human(birth_date_str: str) -> str:
    ymd = calc_age_ymd(birth_date_str)
    if not ymd:
        return ""
    y, m, d = ymd
    return f"{y} ans, {m} mois, {d} jours"


def count_recent_posts(post_dates: List[dt.datetime]) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
    return sum(1 for d in post_dates if d >= cutoff)


# Pendant une recherche : temps écoulé (+total) et Δ depuis la dernière ligne `append_log(..., step=True)`.
_LOG_SEARCH_T0: Optional[float] = None
_LOG_SEARCH_T_PREV: Optional[float] = None


def append_log(text: str, *, step: bool = False) -> None:
    global _LOG_SEARCH_T_PREV
    if step and _LOG_SEARCH_T0 is not None:
        now = time.perf_counter()
        total = now - _LOG_SEARCH_T0
        if _LOG_SEARCH_T_PREV is not None:
            delta = now - _LOG_SEARCH_T_PREV
            text = f"{text}  [+{total:.2f}s | Δ{delta:.2f}s]"
        else:
            text = f"{text}  [+{total:.2f}s]"
        _LOG_SEARCH_T_PREV = now
    ensure_app_folders()
    log_path = os.path.join(LOG_DIR, f"app_{dt.date.today().isoformat()}.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{dt.datetime.now().isoformat(sep=' ', timespec='seconds')}] {text}\n")
    if logger is not None:
        try:
            # Keep Loguru only as console logger here.
            # File persistence is already handled by the manual write above.
            # Adding a file sink on each call creates duplicated handlers and
            # can trigger Windows file-lock/rotation errors.
            logger.info(text)
        except Exception:
            pass
    if LOG_SINK is not None:
        try:
            LOG_SINK(text)
        except Exception:
            pass


LOG_SINK = None


def start_search_log_timing() -> None:
    global _LOG_SEARCH_T0, _LOG_SEARCH_T_PREV
    _LOG_SEARCH_T0 = time.perf_counter()
    _LOG_SEARCH_T_PREV = _LOG_SEARCH_T0


def stop_search_log_timing() -> None:
    global _LOG_SEARCH_T0, _LOG_SEARCH_T_PREV
    _LOG_SEARCH_T0 = None
    _LOG_SEARCH_T_PREV = None


_NET_LAST_TS = {"wikipedia": 0.0, "wikidata": 0.0, "instagram": 0.0}
_WIKIPEDIA_TITLE_CACHE: Dict[str, Optional[str]] = {}
# Set during an active search so Wikipedia/Wikidata waits can be interrupted on cancel.
_search_cancel_event_ref: Optional[threading.Event] = None
# Google SERP HTTP: si 429, mettre en pause un moment pour éviter de spammer.
_GOOGLE_COOLDOWN_UNTIL: float = 0.0


def bind_search_cancel_event(ev: Optional[threading.Event]) -> None:
    global _search_cancel_event_ref
    _search_cancel_event_ref = ev


def _raise_if_cancelled_global() -> None:
    if _search_cancel_event_ref is not None and _search_cancel_event_ref.is_set():
        raise SearchCancelled("Recherche annulée.")


def _sleep_interruptible(seconds: float) -> None:
    """Sleep in short chunks; raise SearchCancelled if user cancelled the search."""
    if seconds <= 0:
        return
    end = time.time() + float(seconds)
    while time.time() < end:
        if _search_cancel_event_ref is not None and _search_cancel_event_ref.is_set():
            raise SearchCancelled("Recherche annulée.")
        time.sleep(min(0.2, max(0.0, end - time.time())))


class RetryManager:
    retries = 3
    backoff = [2, 5, 10]

    def run(self, label: str, fn):
        last_exc = None
        for attempt in range(1, self.retries + 1):
            try:
                append_log(f"[RetryManager] {label} tentative {attempt}/{self.retries}", step=True)
                return fn()
            except SearchCancelled:
                raise
            except Exception as e:
                # Do not retry on "Not Found" / permanent errors.
                if isinstance(e, requests.HTTPError):
                    try:
                        status = e.response.status_code if e.response is not None else None
                    except Exception:
                        status = None
                    if status in (400, 401, 403, 404):
                        append_log(f"[RetryManager] {label} échec permanent HTTP {status}: {e}")
                        raise
                    # 429: respect Retry-After when provided and slow down more aggressively.
                    if status == 429:
                        retry_after = None
                        try:
                            ra = (e.response.headers or {}).get("Retry-After")
                            if ra and str(ra).strip().isdigit():
                                retry_after = int(str(ra).strip())
                        except Exception:
                            retry_after = None
                        base_wait = self.backoff[min(attempt - 1, len(self.backoff) - 1)]
                        wait_s = max(base_wait, retry_after or 30)
                        # add jitter to avoid synchronized retries
                        wait_s = int(wait_s + random.uniform(0, 3))
                        # Cap: Wikimedia Retry-After can be very long; avoid blocking minutes.
                        wait_s = min(wait_s, 25)
                        append_log(f"[RetryManager] {label} 429 Too Many Requests (attente {wait_s}s max)")
                        _sleep_interruptible(wait_s)
                        last_exc = e
                        continue
                last_exc = e
                wait_s = self.backoff[min(attempt - 1, len(self.backoff) - 1)]
                append_log(f"[RetryManager] {label} échec: {e} (attente {wait_s}s)")
                _sleep_interruptible(wait_s)
        raise last_exc


RETRY = RetryManager()
UA = UserAgent() if UserAgent is not None else None


def http_get(url: str, timeout: int = 20) -> requests.Response:
    # Polite throttling for Wikimedia endpoints to reduce 429.
    # Wikipedia API is very sensitive to bursts.
    lower = (url or "").lower()
    if "wikipedia.org" in lower:
        key = "wikipedia"
        min_interval = 1.2
    elif "wikidata.org" in lower:
        key = "wikidata"
        min_interval = 1.0
    elif "instagram.com" in lower:
        key = "instagram"
        min_interval = 1.0
    else:
        key = None
        min_interval = 0.0

    if key is not None:
        now = time.time()
        elapsed = now - _NET_LAST_TS.get(key, 0.0)
        if elapsed < min_interval:
            gap = min_interval - elapsed
            if _search_cancel_event_ref is not None:
                _sleep_interruptible(gap)
            else:
                time.sleep(gap)

    ua = UA.random if UA is not None else (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    headers = {
        "User-Agent": f"{APP_NAME}/{APP_VERSION} (+local app) {ua}",
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    if key is not None:
        _NET_LAST_TS[key] = time.time()
    return resp


def _parse_int_maybe(text: str) -> Optional[int]:
    if not text:
        return None
    t = text.strip().replace("\u202f", " ").replace("\xa0", " ")
    # handle 12.3k / 1.2m style
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*([kKmM])", t)
    if m:
        num = float(m.group(1).replace(",", "."))
        mult = 1000 if m.group(2).lower() == "k" else 1_000_000
        return int(num * mult)
    m2 = re.search(r"(\d[\d\s,\.]*)", t)
    if not m2:
        return None
    digits = re.sub(r"[^\d]", "", m2.group(1))
    return int(digits) if digits else None


def instagram_public_scrape(profile_url: str) -> dict:
    """
    Best-effort public Instagram scrape without login.
    Returns dict: bio (str|None), followers (int|None), posts (int|None)
    Never raises (returns empty values on failure).
    """
    out = {"bio": None, "followers": None, "posts": None}
    url = (profile_url or "").strip().rstrip("/")
    if not url:
        return out
    if "instagram.com" not in url:
        return out

    try:
        r = http_get(url + "/", timeout=20)
        if r.status_code in (401, 403, 429):
            return out
        r.raise_for_status()
        html_text = r.text or ""
    except Exception:
        return out

    # Try meta description first (often contains followers/posts and sometimes bio).
    desc = None
    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                desc = meta["content"]
        except Exception:
            desc = None
    if desc is None:
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']', html_text, re.I)
        if m:
            desc = html.unescape(m.group(1))

    if desc:
        # Example: "1,234 Followers, 56 Following, 10 Posts - See Instagram photos and videos from Name (@user) ..."
        parts = desc.split(" - ", 1)
        head = parts[0]
        m_follow = re.search(r"([\d\.,\s\u202f\xa0]+[kKmM]?)\s+Followers", head, re.I)
        m_posts = re.search(r"([\d\.,\s\u202f\xa0]+[kKmM]?)\s+Posts", head, re.I)
        if m_follow:
            out["followers"] = _parse_int_maybe(m_follow.group(1))
        if m_posts:
            out["posts"] = _parse_int_maybe(m_posts.group(1))
        # Bio sometimes appears after the second dash in some locales; best-effort keep tail.
        if len(parts) > 1:
            tail = parts[1].strip()
            if tail and len(tail) < 220:
                out["bio"] = tail

    return out


def wikidata_entity_is_human(ent: dict) -> bool:
    """True if Wikidata entity has P31 -> Q5 (human). Filters out clubs, seasons, competitions."""
    try:
        for c in (ent.get("claims") or {}).get("P31") or []:
            sn = (c or {}).get("mainsnak") or {}
            dv = sn.get("datavalue", {}).get("value")
            if isinstance(dv, dict) and dv.get("id") == "Q5":
                return True
    except Exception:
        return False
    return False


def wikidata_get_entity(qid: str) -> dict:
    url = (
        "https://www.wikidata.org/w/api.php?"
        f"action=wbgetentities&ids={quote_plus(qid)}&props=claims|labels&languages=fr|en&format=json"
    )

    def _do():
        r = http_get(url)
        r.raise_for_status()
        return r.json()

    return RETRY.run(f"Wikidata entity {qid}", _do)


def wikidata_search_entity(
    name: str, *, require_human: bool = True, limit: int = 10
) -> Optional[str]:
    """
    Wikidata text search. For athletes, require_human=True skips clubs/teams as first hits.
    For team names (wikidata_team_players), pass require_human=False.
    """
    url = (
        "https://www.wikidata.org/w/api.php?"
        f"action=wbsearchentities&search={quote_plus(name)}&language=en&format=json"
        f"&limit={max(3, min(int(limit), 20))}"
    )

    def _do():
        r = http_get(url)
        r.raise_for_status()
        return r.json()

    data = RETRY.run(f"Wikidata search {name}", _do)
    items = (data or {}).get("search") or []
    if not require_human:
        for item in items:
            qid = item.get("id")
            if isinstance(qid, str) and qid.startswith("Q"):
                return qid
        return None
    for item in items:
        qid = item.get("id")
        if not isinstance(qid, str) or not qid.startswith("Q"):
            continue
        wd = wikidata_get_entity(qid)
        ent = wd.get("entities", {}).get(qid, {})
        if wikidata_entity_is_human(ent):
            return qid
    return None


def wikidata_sparql(query: str) -> dict:
    url = "https://query.wikidata.org/sparql"

    def _do():
        r = requests.get(
            url,
            params={"format": "json", "query": query},
            headers={"User-Agent": (UA.random if UA is not None else "AthletesSearcher/1.0"), "Accept": "application/sparql-results+json"},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()

    return RETRY.run("Wikidata SPARQL", _do)


def is_likely_player_name_for_roster(name: str) -> bool:
    """Heuristic: table cell / link text looks like a player full name (2–3 tokens)."""
    n = (name or "").strip()
    if not n:
        return False
    lower = n.lower()
    blocked_tokens = [
        "fc", "cf", "ac", "sc", "inter", "bayern", "sporting", "eintracht", "saint-germain",
        "paris", "milan", "frankfurt", "são paulo", "sao paulo", "loan", "captain", "manager",
        "coach", "league", "cup", "women", "youth", "reserve", "academy",
    ]
    # Faux positifs fréquents (langues, Wikipédia) captés par la regex ou des tableaux hors effectif.
    blocked_first_word = {
        "bahasa", "basa", "lingua", "batak", "bikol", "jaku", "yerwa", "pidgin", "fiji",
        "ghanaian", "gõychi", "wikimedia", "na",
    }
    first_w = lower.split()[0].strip(".,;:") if lower else ""
    if first_w in blocked_first_word:
        return False
    if any(tok in lower for tok in blocked_tokens):
        return False
    if not re.match(r"^[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]+){1,2}$", n):
        return False
    return True


def roster_name_from_player_profile_href(href: str) -> Optional[str]:
    """
    Many club sites (e.g. psg.fr) use <a href="/joueurs/slug-name"> with link text
    « Défenseur », « Gardien de but », etc. Derive a display name from the URL slug.
    """
    try:
        raw = (href or "").strip()
        if not raw:
            return None
        if raw.startswith("//"):
            raw = "https:" + raw
        p = urlparse(raw)
        path = (p.path or "").strip("/")
        if not path:
            return None
        segs = [s for s in path.split("/") if s]
        slug = None
        for i, s in enumerate(segs):
            if s.lower() in ("joueurs", "joueur", "players", "player", "equipe", "squad"):
                if i + 1 < len(segs):
                    slug = segs[i + 1]
                break
        if not slug:
            slug = segs[-1] if segs else None
        if not slug or len(slug) < 3:
            return None
        parts = [x for x in slug.replace("_", "-").split("-") if x and not x.isdigit()]
        if len(parts) >= 2:
            name = " ".join((w[:1].upper() + w[1:].lower()) if w else "" for w in parts)
            if not is_likely_player_name_for_roster(name):
                return None
            return name
        if len(parts) == 1 and len(parts[0]) >= 5:
            w = parts[0]
            name = w[:1].upper() + w[1:].lower()
            # Mononyme (ex. Vitinha, Marquinhos) — pas le même heuristique que les noms complets.
            if re.match(r"^[A-ZÀ-Ý][a-zà-ÿA-Za-zÀ-ÿ'\-]{3,}$", name):
                return name
        return None
    except Exception:
        return None


def _normalize_uppercase_surname_line(line: str) -> Optional[str]:
    """
    Lignes du type « Remi ANDREU », « Jean Baptiste PIGEAUD » (prénom(s) + nom en majuscules).
    Convertit en « Remi Andreu » pour passer is_likely_player_name_for_roster.
    """
    line = (line or "").strip()
    if len(line) < 4 or len(line) > 72:
        return None
    if re.match(r"^[A-ZÀ-Ý]{1,3}$", line):
        return None
    parts = line.split()
    if len(parts) < 2:
        return None
    start_surname = None
    for i, p in enumerate(parts):
        letters = "".join(c for c in p if c.isalpha())
        if len(letters) >= 2 and letters == letters.upper():
            start_surname = i
            break
    if start_surname is None or start_surname == 0:
        return None

    def tw(w: str) -> str:
        if not w:
            return w
        return w[0].upper() + w[1:].lower() if w[0].isalpha() else w

    cand = " ".join(tw(x) for x in parts)
    if not is_likely_player_name_for_roster(cand):
        return None
    return cand


def _extract_roster_names_plaintext_caps_surname(html_text: str, limit: int) -> List[str]:
    """Extraction générique « Prénom NOM » (effectif en texte, pas seulement liens /joueurs/)."""
    if BeautifulSoup is None or not (html_text or "").strip():
        return []
    soup = BeautifulSoup(html_text, "html.parser")
    blob = soup.get_text("\n")
    out: List[str] = []
    seen: set = set()
    skip_sub = (
        "feuille",
        "fédérale",
        "federale",
        "national",
        "régional",
        "effectif —",
        "suivre",
        "signaler",
        "mentions légales",
        "cookies",
        "recevez les résultats",
    )
    for raw in blob.split("\n"):
        line = raw.strip()
        if not line or len(line) < 4:
            continue
        low = line.lower()
        if any(x in low for x in skip_sub):
            continue
        if "@" in line or "http" in low:
            continue
        if line.replace(".", "").isdigit():
            continue
        cand = _normalize_uppercase_surname_line(line)
        if not cand:
            continue
        k = cand.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def resolve_official_club_roster_url(club: str) -> Optional[str]:
    """Return official squad/effectif URL when known; keys are normalized."""
    club_key = (club or "").strip().lower()
    if not club_key:
        return None
    if club_key in OFFICIAL_CLUB_ROSTER_URLS:
        return OFFICIAL_CLUB_ROSTER_URLS[club_key]
    for alias, url in OFFICIAL_CLUB_ROSTER_URLS.items():
        if alias in club_key or club_key in alias:
            return url
    return None


# Hosts that aggregate squads (prefer club official pages when possible).
_ROSTER_AGGREGATOR_HOST_MARKERS = (
    "transfermarkt",
    "flashscore",
    "sofascore",
    "fotmob",
    "whoscored",
    "footmercato",
    "rmcsport",
    "lequipe.fr",
    "goal.com",
    "livescore",
    "soccerway",
    "worldfootball",
    "besoccer",
    "365scores",
    "maxifoot",
    "eurosport",
    "skysports",
    "espn.com",
    "footalist",
    "footbalist",
    "mercato.fr",
)


def _score_roster_candidate_url(
    href: str,
    *,
    prefer_feminine: bool = False,
    club_hint: str = "",
    ville_hint: str = "",
    sport_hint: str = "",
) -> int:
    """Higher = more likely official squad page; aggregators strongly penalized."""
    h = (href or "").strip()
    if not h.startswith("http"):
        return -10_000
    low = h.lower()
    if "wikipedia.org" in low:
        return -10_000
    if any(x in low for x in ("facebook.com", "twitter.com", "instagram.com", "youtube.com", "tiktok.com")):
        return -10_000
    if "google." in low and "/url" not in low:
        return -10_000
    score = 0
    for m in _ROSTER_AGGREGATOR_HOST_MARKERS:
        if m in low:
            score -= 500
            break
    try:
        netloc = (urlparse(h).hostname or "").lower()
        path = (urlparse(h).path or "").lower()
    except Exception:
        netloc, path = "", ""

    if netloc.endswith(".fr"):
        score += 18
    # Pénalise fortement les sites purement « médias / news » pour l'effectif.
    media_hints = [
        "rugbyrama.",
        "madeinfoot.",
        "madeinparisiens.",
        "lequipe.",
        "footmercato.",
        "rmcsport.",
        "eurosport.",
        "sports.fr",
        "francebleu.",
        "lindependant.",
    ]
    if any(mh in netloc for mh in media_hints):
        score -= 80
    # Préférer une page profonde (club / effectif) à une page d’accueil générique.
    path_trim = path.rstrip("/")
    depth = path_trim.count("/") if path_trim else 0
    if depth <= 1 and len(path_trim) <= 1:
        score -= 35
    elif depth >= 2:
        score += 8

    if any(m in path for m in ("effectif", "equipe", "squad", "team", "roster", "liste-joueurs")):
        score += 52
    elif any(m in path for m in ("player", "joueur", "profil")):
        score += 22

    # Éviter de mélanger effectifs féminin / masculin quand l’IHM indique une équipe.
    if prefer_feminine:
        if any(m in path for m in ("feminin", "feminine", "women", "femmes", "females", "féminin")):
            score += 90
        if any(m in path for m in ("masculin", "mens", "hommes", "men")) and not any(
            x in path for x in ("feminin", "feminine", "femmes", "féminin")
        ):
            score -= 100
    else:
        if any(m in path for m in ("feminin", "feminine", "women", "femmes", "females", "féminin")):
            score -= 80
        if any(m in path for m in ("masculin", "mens", "hommes", "men")):
            score += 60

    # Bonus si le domaine/chemin contient le nom du club / de la ville / du sport;
    # pénalité si aucun de ces tokens n'apparaît (évite par ex. psg.fr quand le club ne contient pas "psg").
    tokens: List[str] = []
    for raw in (club_hint, ville_hint, sport_hint):
        for t in re.findall(r"[A-Za-zÀ-ÿ0-9]+", (raw or "").lower()):
            if len(t) >= 3:
                tokens.append(t)
    txt = f"{netloc} {path}"
    # Sigles courts (ex "RCC") : si une ville est fournie, on exige au moins un token ville,
    # sinon on risque de tomber sur un autre club homonyme.
    club_clean = "".join(re.findall(r"[A-Za-z0-9]+", (club_hint or "").strip()))
    if club_clean and len(club_clean) <= 4 and (ville_hint or "").strip():
        ville_tokens = [
            t
            for t in re.findall(r"[A-Za-zÀ-ÿ0-9]+", (ville_hint or "").lower())
            if len(t) >= 3
        ]
        if ville_tokens and not any(vt in txt for vt in ville_tokens):
            score -= 140
    if tokens:
        if any(t in txt for t in tokens):
            score += 35
        else:
            score -= 90
    return score


def _pick_roster_page_url(
    candidates: List[str],
    *,
    prefer_feminine: bool = False,
    club_hint: str = "",
    ville_hint: str = "",
    sport_hint: str = "",
) -> Optional[str]:
    """Best URL for squad/effectif: score SERP candidates, skip wiki/social/aggregators when possible."""
    best: Optional[str] = None
    best_score = -10_000
    for href in candidates:
        h = (href or "").strip()
        if not h or not h.startswith("http"):
            continue
        s = _score_roster_candidate_url(
            h,
            prefer_feminine=prefer_feminine,
            club_hint=club_hint,
            ville_hint=ville_hint,
            sport_hint=sport_hint,
        )
        if s > best_score:
            best_score = s
            best = h
    # Need a plausible roster URL: path hint or strong net score (e.g. official .fr with equipe path).
    if best is None or best_score < 5:
        return None
    return best


def _dedupe_urls_preserve_order(urls: List[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for u in urls:
        u = (u or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def _extract_urls_from_google_serp_html(html: str) -> List[str]:
    """Decode /url?q=... links from a Google results HTML page."""
    out: List[str] = []
    for m in re.findall(r'href="(/url\?[^"]+)"', html or ""):
        try:
            qpart = m.split("?", 1)[-1]
            qs = parse_qs(qpart)
            qv = (qs.get("q") or [None])[0]
            if qv and str(qv).startswith("http"):
                out.append(unquote(str(qv)))
        except Exception:
            continue
    for m in re.findall(r'href="(https?://[^"]+)"', html or ""):
        ml = m.lower()
        if "google." in ml or "gstatic.com" in ml:
            continue
        out.append(m)
    # de-dup preserving order
    seen = set()
    uniq: List[str] = []
    for u in out:
        if u in seen:
            continue
        seen.add(u)
        uniq.append(u)
    return uniq


def _google_serp_html_for_query(query: str) -> str:
    url = f"https://www.google.com/search?hl=fr&num=10&q={quote_plus(query)}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }
    r = requests.get(url, headers=headers, timeout=22)
    r.raise_for_status()
    return r.text or ""


def _ddg_text_blob_for_birth_query(query: str) -> str:
    """Titres + extraits DuckDuckGo (évite dépendre uniquement de Google, ex. HTTP 429)."""
    _raise_if_cancelled_global()
    try:
        from ddgs import DDGS
    except Exception:
        try:
            from duckduckgo_search import DDGS
        except Exception:
            return ""
    try:
        ddgs = DDGS()
        rows = list(ddgs.text((query or "").strip(), max_results=12))
    except Exception:
        return ""
    parts: List[str] = []
    for r in rows:
        _raise_if_cancelled_global()
        parts.append(
            " ".join(
                x
                for x in ((r.get("title") or ""), (r.get("body") or ""), (r.get("href") or ""))
                if x
            )
        )
    return " ".join(parts)


def _birth_year_plausible(y: int) -> bool:
    cy = dt.datetime.now().year
    # ~10 ans min pour sportifs seniors/jeunes en compétition
    return 1970 <= y <= min(cy - 8, 2022)


def _best_birth_date_from_plain_text(plain: str) -> Optional[str]:
    """Pick a plausible YYYY-MM-DD from visible text (SERP snippets, fiches sport, etc.)."""
    if not plain:
        return None
    candidates: List[Tuple[str, int]] = []
    low = plain.lower()
    # Mots-clés proches d’une identité / fiche joueur (sans cibler un site précis).
    ctx_keywords_strong = (
        "naissance",
        "born",
        "né",
        "née",
        "birth",
        "geboren",
        "âge",
        "age",
    )
    ctx_keywords_soft = (
        "joueur",
        "player",
        "rugby",
        "football",
        "profil",
        "français",
        "francais",
        "fiche",
        "kg",
        "itsrugby",
        "ffr",
        "club",
    )
    for m in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b", plain):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _birth_year_plausible(y) and 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                dt.datetime(y, mo, d)
            except ValueError:
                continue
            s = f"{y:04d}-{mo:02d}-{d:02d}"
            ctx = low[max(0, m.start() - 100) : m.end() + 100]
            bonus = 0
            if any(x in ctx for x in ctx_keywords_strong):
                bonus += 10
            if any(x in ctx for x in ctx_keywords_soft):
                bonus += 5
            candidates.append((s, bonus))
    for m in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", plain):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if _birth_year_plausible(y) and 1 <= mo <= 12 and 1 <= d <= 31:
            try:
                dt.datetime(y, mo, d)
            except ValueError:
                continue
            s = f"{y:04d}-{mo:02d}-{d:02d}"
            ctx = low[max(0, m.start() - 100) : m.end() + 100]
            bonus = 0
            if any(x in ctx for x in ctx_keywords_strong):
                bonus += 10
            if any(x in ctx for x in ctx_keywords_soft):
                bonus += 5
            candidates.append((s, bonus))
    if not candidates:
        return None
    best_score = max(c[1] for c in candidates)
    best = [c for c in candidates if c[1] == best_score]
    # Trop de dates différentes sans contexte → on évite un faux positif.
    if best_score == 0 and len({c[0] for c in candidates}) > 3:
        return None
    # Parmi les meilleurs scores, première occurrence dans le texte (ordre des regex).
    return best[0][0]


def serp_guess_birth_date(
    name: str, club_hint: str = "", sport_hint: str = "", ville_hint: str = ""
) -> Optional[str]:
    """
    Best-effort date de naissance depuis extraits moteurs (Google puis DDG en secours).
    Requêtes proches de ce qu’un utilisateur taperait (nom + club / ville + âge / naissance).
    """
    nm = (name or "").strip()
    if len(nm) < 3:
        return None
    append_log(f"{nm}: recherche date de naissance (web)…", step=True)
    ch = (club_hint or "").strip()
    sp = (sport_hint or "").strip()
    vh = (ville_hint or "").strip()
    # Ordre: d’abord ce qu’un utilisateur taperait (nom + club/ville + âge), puis requêtes génériques.
    queries: List[str] = []
    if ch:
        queries.extend(
            [
                f'"{nm}" {ch} âge',
                f'"{nm}" {ch} age',
                f'"{nm}" {ch} date de naissance',
            ]
        )
    if vh and vh.lower() != ch.lower():
        queries.extend(
            [
                f'"{nm}" {vh} âge',
                f'"{nm}" {vh} age',
                f'"{nm}" {vh} date de naissance',
            ]
        )
    queries.extend([f'"{nm}" date de naissance', f'"{nm}" né'])
    if sp:
        queries.extend([f'"{nm}" {sp} né', f'"{nm}" {sp} date de naissance'])
    seen: set = set()
    for q in queries:
        _raise_if_cancelled_global()
        q = q.strip()
        if not q or q in seen:
            continue
        seen.add(q)
        plain = ""
        try:
            html = _google_serp_html_for_query(q)
            plain = re.sub(r"<[^>]+>", " ", html)
            plain = re.sub(r"\s+", " ", plain)
        except Exception:
            plain = ""
        if not plain or not _best_birth_date_from_plain_text(plain):
            plain = (plain + " " + _ddg_text_blob_for_birth_query(q)).strip()
        bd = _best_birth_date_from_plain_text(plain)
        if bd:
            append_log(f"{nm}: date de naissance (web) {bd}", step=True)
            return bd
    append_log(f"{nm}: date de naissance (web) introuvable", step=True)
    return None


def _roster_discovery_queries(
    club: str,
    ville: str,
    sport: str,
    *,
    prefer_feminine: bool,
    season_start_year: Optional[int] = None,
) -> List[str]:
    """
    Requêtes Google/DDGS pour trouver une page d’effectif (sport / club / ville saisis par l’utilisateur).
    Objectif: peu de requêtes, très ciblées.
    """
    club = (club or "").strip()
    ville = (ville or "").strip()
    sp = (sport or "").strip()
    gender = "féminin" if prefer_feminine else "masculin"
    season_part = ""
    if season_start_year is not None:
        try:
            y = int(season_start_year)
            season_part = f"{y}-{y+1}"
        except Exception:
            season_part = ""

    base = f"{club} {ville} {sp}".strip()
    if not base:
        return []

    queries: List[str] = []
    # 1) Requête principale, la plus proche de ce que tu tapes manuellement.
    if season_part:
        queries.append(f"{base} effectif {season_part} {gender} site officiel".strip())
    else:
        queries.append(f"{base} effectif {gender} site officiel".strip())

    # 2) Une seule requête de secours, plus large (sans saison / sans « site officiel »).
    queries.append(f"{base} effectif {gender}".strip())
    # Nettoyage basique
    seen: set = set()
    out: List[str] = []
    for q in queries:
        q = " ".join(q.split())
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def discover_official_roster_url_google_http(
    club: str,
    ville: str,
    *,
    prefer_feminine: bool = False,
    sport: str = "",
    season_start_year: Optional[int] = None,
) -> Optional[str]:
    """
    Same idea as typing in Google: « nom du club effectif » (HTTP SERP, no Selenium).
    Agrège les liens de toutes les requêtes puis choisit le meilleur score (masculin vs féminin).
    """
    global _GOOGLE_COOLDOWN_UNTIL
    club = (club or "").strip()
    if not club:
        return None
    ville = (ville or "").strip()
    raw_queries = _roster_discovery_queries(
        club,
        ville,
        sport,
        prefer_feminine=prefer_feminine,
        season_start_year=season_start_year,
    )
    seen_q: set = set()
    queries: List[str] = []
    for q in raw_queries:
        q = (q or "").strip()
        if q and q not in seen_q:
            seen_q.add(q)
            queries.append(q)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/",
    }
    all_urls: List[str] = []
    now_ts = time.time()
    if _GOOGLE_COOLDOWN_UNTIL and now_ts < _GOOGLE_COOLDOWN_UNTIL:
        append_log(f"Google (effectif): cooldown actif ({int(_GOOGLE_COOLDOWN_UNTIL - now_ts)}s)", step=True)
        return None
    for q in queries:
        _raise_if_cancelled_global()
        if not q.strip():
            continue
        url = f"https://www.google.com/search?hl=fr&num=20&q={quote_plus(q)}"
        try:
            append_log(f"Google (effectif): requête « {q} »", step=True)
            resp = requests.get(url, headers=headers, timeout=20)
            if resp.status_code >= 400:
                append_log(f"Google (effectif): HTTP {resp.status_code}", step=True)
                if resp.status_code == 429:
                    # Evite 3 requêtes inutiles + risque de blocage plus long.
                    _GOOGLE_COOLDOWN_UNTIL = time.time() + 60.0
                    break
                continue
            all_urls.extend(_extract_urls_from_google_serp_html(resp.text or ""))
        except Exception as ex:
            append_log(f"Google (effectif): erreur ({ex})", step=True)
            continue
    urls = _dedupe_urls_preserve_order(all_urls)
    picked = urls[0] if urls else None
    if picked:
        append_log(f"Google (effectif): URL retenue {picked}", step=True)
    return picked


def discover_official_roster_url_ddgs(
    club: str,
    ville: str,
    *,
    prefer_feminine: bool = False,
    sport: str = "",
    season_start_year: Optional[int] = None,
) -> Optional[str]:
    """Fallback: DuckDuckGo text search (similar intent to Google)."""
    try:
        from ddgs import DDGS
    except Exception:
        return None
    raw_queries = _roster_discovery_queries(
        club,
        ville,
        sport,
        prefer_feminine=prefer_feminine,
        season_start_year=season_start_year,
    )
    seen_q: set = set()
    queries: List[str] = []
    for q in raw_queries:
        q = (q or "").strip()
        if q and q not in seen_q:
            seen_q.add(q)
            queries.append(q)
    try:
        ddgs = DDGS()
    except Exception:
        return None
    all_hrefs: List[str] = []
    for q in queries:
        if not (q or "").strip():
            continue
        try:
            rows = list(ddgs.text(q, max_results=15))
        except Exception:
            continue
        all_hrefs.extend([(r.get("href") or "").strip() for r in rows if r])
    hrefs = _dedupe_urls_preserve_order(all_hrefs)
    picked = hrefs[0] if hrefs else None
    if picked:
        append_log(f"DDGS (effectif): URL retenue {picked}", step=True)
    return picked


def official_site_extract_names_from_url(url: str, limit: int) -> List[str]:
    def _fetch():
        r = http_get(url, timeout=35)
        r.raise_for_status()
        return r.text

    html_text = RETRY.run(f"Site officiel html {url}", _fetch)
    names: List[str] = []
    seen = set()

    def _add(name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        if not is_likely_player_name_for_roster(n):
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(n)

    if BeautifulSoup is not None:
        soup = BeautifulSoup(html_text, "html.parser")
        # 1) Links that look like player profiles (common on club sites)
        for a in soup.select(
            'a[href*="player"], a[href*="joueur"], a[href*="profil"], '
            'a[href*="/players/"], a[href*="/joueurs/"]'
        ):
            href = (a.get("href") or "").strip()
            if not href:
                continue
            href_l = href.lower()
            if any(x in href_l for x in ("coach", "staff", "news", "ticket", "shop", "club", "legal")):
                continue
            txt = (a.get_text() or "").strip()
            candidate = None
            if txt and len(txt) <= 60 and " " in txt and is_likely_player_name_for_roster(txt):
                candidate = txt
            if not candidate:
                candidate = roster_name_from_player_profile_href(href)
            if candidate:
                _add(candidate)
                if len(names) >= limit:
                    return names
        # 2) Table cells (squad lists)
        for tbl in soup.select("table")[:12]:
            for tr in tbl.select("tr"):
                for td in tr.select("td, th"):
                    txt = (td.get_text() or "").strip()
                    if "\n" in txt:
                        txt = txt.split("\n")[0].strip()
                    if is_likely_player_name_for_roster(txt):
                        _add(txt)
                        if len(names) >= limit:
                            return names

    # 3) HTML brut (sans BeautifulSoup ou en complément) : slugs /joueurs/… / /players/…
    # (ex. psg.fr : libellé du lien = « Défenseur », pas le nom — le slug suffit.)
    raw_slugs: List[str] = []
    for m in re.finditer(r"/(?:joueurs|players)/([a-z0-9\-]+)", html_text, re.I):
        slug = (m.group(1) or "").strip("-")
        if len(slug) < 3 or slug.lower() in ("joueur", "player", "football", "effectif"):
            continue
        raw_slugs.append(slug)
    uniq_slugs = list(dict.fromkeys(raw_slugs))
    # Évite les doublons « dro-ferna » vs « dro-fernandez » (slug court = préfixe du long).
    filtered_slugs: List[str] = []
    for s in uniq_slugs:
        if any(t != s and t.startswith(s + "-") for t in uniq_slugs):
            continue
        filtered_slugs.append(s)
    for slug in filtered_slugs:
        cand = roster_name_from_player_profile_href(f"/joueurs/{slug}")
        if cand:
            _add(cand)
            if len(names) >= limit:
                return names
    # 4) Texte brut « Prénom NOM » (certains sites d’effectif n’exposent que du texte).
    if len(names) < limit:
        for n in _extract_roster_names_plaintext_caps_surname(html_text, limit - len(names)):
            _add(n)
            if len(names) >= limit:
                break
    return names


def wikipedia_find_page_title(search_query: str, lang: str = "fr") -> Optional[str]:
    lang = (lang or "fr").strip().lower()
    if lang not in ("en", "fr"):
        lang = "fr"
    key = f"{lang}:{(search_query or '').strip().lower()}"
    if key in _WIKIPEDIA_TITLE_CACHE:
        return _WIKIPEDIA_TITLE_CACHE[key]
    search_url = (
        f"https://{lang}.wikipedia.org/w/api.php?"
        f"action=query&list=search&srsearch={quote_plus(search_query)}&srlimit=1&format=json"
    )

    def _do():
        r = http_get(search_url)
        r.raise_for_status()
        data = r.json()
        hit = (((data or {}).get("query") or {}).get("search") or [])
        if not hit:
            return None
        return hit[0].get("title")

    title = RETRY.run(f"Wikipedia search title ({lang}) {search_query}", _do)
    _WIKIPEDIA_TITLE_CACHE[key] = title
    return title


def _wikipedia_find_section_anchor(soup, hint_raw: str):
    """Trouve l’ancre de section (id ou span.mw-headline), y compris titres accentués."""
    h = (hint_raw or "").strip()
    if not h:
        return None
    sec_id = h.replace(" ", "_")
    anchor = soup.find(id=re.compile(rf"^{re.escape(sec_id)}$", re.IGNORECASE))
    if anchor is not None:
        return anchor
    hlow = h.lower()
    for hx in soup.find_all(["h2", "h3", "h4"]):
        span = hx.find("span", class_="mw-headline")
        if span and span.get_text(strip=True).lower() == hlow:
            return span
    return None


def _wikipedia_table_looks_like_squad(tbl) -> bool:
    """Filtre les wikitables hors effectif (navbox, langues, etc.)."""
    for tr in tbl.select("tr")[:5]:
        row_text = " ".join((c.get_text() or "").lower() for c in tr.find_all(["th", "td"]))
        if any(
            k in row_text
            for k in (
                "joueur",
                "joueurs",
                "nom",
                "player",
                "players",
                "footballeur",
                "poste",
                "position",
                "numéro",
                "n°",
                "pays",
                "nationalité",
                "capitaine",
            )
        ):
            return True
    return False


def append_roster_preview_log(label: str, names: List[str], max_show: int = 30) -> None:
    """Log lisible de l’effectif (tronqué si très long)."""
    if not names:
        append_log(f"{label}: 0 noms.", step=True)
        return
    n = len(names)
    if n <= max_show:
        append_log(f"{label}: {n} noms — {', '.join(names)}", step=True)
    else:
        head = ", ".join(names[:max_show])
        append_log(f"{label}: {n} noms — {head} … (+{n - max_show} autres)", step=True)


def wikipedia_extract_names_from_page(
    title: str,
    limit: int = 60,
    section_hints: Optional[List[str]] = None,
    lang: str = "fr",
    strict_current_squad: bool = False,
) -> List[str]:
    """
    Best-effort extraction of player-like names from a Wikipedia page (fr or en).
    Uniquement à partir des liens dans des tableaux d’effectif (pas de regex sur tout le HTML :
    cela injectait des noms de langues / bruit).
    Si strict_current_squad est True, pas de balayage de toutes les wikitables de la page.
    """
    lang = (lang or "fr").strip().lower()
    if lang not in ("en", "fr"):
        lang = "fr"
    url = f"https://{lang}.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"

    def _do():
        r = http_get(url, timeout=30)
        r.raise_for_status()
        return r.text

    html_text = RETRY.run(f"Wikipedia html ({lang}) {title}", _do)
    names: List[str] = []
    seen = set()

    def _add(name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        if not is_likely_player_name_for_roster(n):
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(n)

    raw_hints = [h.strip() for h in (section_hints or []) if (h or "").strip()]
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html_text, "html.parser")
        scoped_tables = []
        if raw_hints:
            for h in raw_hints:
                anchor = _wikipedia_find_section_anchor(soup, h)
                if anchor is not None:
                    headline = anchor
                    for _ in range(3):
                        if headline and headline.name in ("h2", "h3", "h4"):
                            break
                        headline = headline.parent
                    node = headline
                    for _ in range(60):
                        if node is None:
                            break
                        node = node.find_next_sibling()
                        if node is None:
                            break
                        if node.name in ("h2", "h3", "h4"):
                            break
                        if getattr(node, "name", None) == "table" and "wikitable" in (node.get("class") or []):
                            scoped_tables.append(node)
                            break
                if scoped_tables:
                    break

        if scoped_tables:
            tables = scoped_tables
        elif strict_current_squad:
            tables = []
        else:
            tables = [t for t in soup.select("table.wikitable") if _wikipedia_table_looks_like_squad(t)]
        from_section = bool(scoped_tables)
        for tbl in tables[:6]:
            if not from_section and not _wikipedia_table_looks_like_squad(tbl):
                continue
            player_col_idx = None
            header_cells = tbl.select("tr th")
            for idx, th in enumerate(header_cells[:12]):
                htxt = (th.get_text(" ", strip=True) or "").lower()
                if any(k in htxt for k in ("player", "name", "squad", "joueur", "joueurs", "nom")):
                    player_col_idx = idx
                    break

            rows = tbl.select("tr")
            for tr in rows:
                cells = tr.find_all(["th", "td"])
                if not cells:
                    continue
                candidate_links = []
                if player_col_idx is not None and player_col_idx < len(cells):
                    candidate_links = cells[player_col_idx].select("a[href^='/wiki/']")
                elif len(cells) > 1:
                    candidate_links = cells[1].select("a[href^='/wiki/']")

                for a in candidate_links:
                    txt = (a.get_text() or "").strip()
                    if not txt or ":" in txt:
                        continue
                    href = (a.get("href") or "").lower()
                    if "/wiki/" in href and any(
                        x in href for x in ("langue_", "language_", "liste_des_langues", "linguistique")
                    ):
                        continue
                    _add(txt)
                    if len(names) >= limit:
                        return names
    return names


def _wikipedia_title_score_player(nm: str, title: str) -> int:
    """Prefer biographical titles; penalize seasons, clubs, competitions (first search hit is often wrong)."""
    nm = (nm or "").strip()
    title = (title or "").strip()
    low = title.lower()
    nm_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", nm) if len(t) >= 3]
    score = 0
    if nm_tokens and len(nm_tokens) >= 2 and all(t in low for t in nm_tokens[:2]):
        score += 120
    elif nm_tokens and nm_tokens[0] in low:
        score += 40
    if "(" in title and ")" in title:
        score += 12
    penalties = (
        "saison ",
        "championnat",
        "rugby club",
        "club de football",
        "club de rugby",
        "lnr",
        "top 14",
        "fédération",
        "équipe de",
        "coupe d'",
        "coupe ",
        "europe",
        "liste des",
        "national ",
    )
    for p in penalties:
        if p in low:
            score -= 100
    return score


def wikipedia_resolve_wikidata_qid(name: str, club_hint: str = "") -> Optional[str]:
    """
    Resolve a player's Wikidata item through French Wikipedia first (same locale as the app).
    Only returns Q-ids for humans (Q5), so club/season pages are skipped.
    """
    nm = (name or "").strip()
    if not nm:
        return None
    q = f'"{nm}" {club_hint}'.strip()
    search_url = (
        "https://fr.wikipedia.org/w/api.php?"
        f"action=query&list=search&srsearch={quote_plus(q)}&srlimit=8&format=json"
    )

    def _search():
        r = http_get(search_url)
        r.raise_for_status()
        return r.json()

    try:
        data = RETRY.run(f"Wikipedia search (qid) {nm}", _search)
        hits = (((data or {}).get("query") or {}).get("search") or [])
        if not hits:
            return None
        ordered = sorted(
            hits,
            key=lambda h: _wikipedia_title_score_player(nm, (h.get("title") or "")),
            reverse=True,
        )
        for h in ordered:
            chosen_title = (h.get("title") or "").strip()
            if not chosen_title:
                continue
            pageprops_url = (
                "https://fr.wikipedia.org/w/api.php?"
                f"action=query&prop=pageprops&ppprop=wikibase_item&titles={quote_plus(chosen_title)}&format=json"
            )

            def _props():
                r = http_get(pageprops_url)
                r.raise_for_status()
                return r.json()

            pdata = RETRY.run(f"Wikipedia pageprops {chosen_title}", _props)
            pages = ((pdata or {}).get("query") or {}).get("pages") or {}
            first_page = next(iter(pages.values())) if pages else {}
            qid = ((first_page or {}).get("pageprops") or {}).get("wikibase_item")
            if not (isinstance(qid, str) and qid.startswith("Q")):
                continue
            wd = wikidata_get_entity(qid)
            ent = wd.get("entities", {}).get(qid, {})
            if wikidata_entity_is_human(ent):
                return qid
    except Exception:
        return None
    return None


def wikidata_team_players(team_name: str, limit: int = 30, season_start_year: Optional[int] = None) -> List[dict]:
    """
    Best-effort roster-like list from Wikidata (membership on team item).
    La requête utilise l’occupation « joueur de football » (P106) dans Wikidata :
    les autres sports peuvent renvoyer peu ou pas de résultats; Wikipedia / site officiel
    restent les sources génériques.
    Returns list of dicts: name, birth_date, nationality, instagram.
    """
    team_qid = CLUB_WIKIDATA_QIDS.get((team_name or "").strip().lower())
    if not team_qid:
        team_qid = wikidata_search_entity(team_name, require_human=False)
    if not team_qid:
        return []
    # Season filter: keep players whose membership has no end date,
    # or ends after season start (default: last 18 months).
    if season_start_year is not None:
        season_start_dt = f"{int(season_start_year)}-07-01T00:00:00Z"
    else:
        season_start_dt = (dt.datetime.now() - dt.timedelta(days=540)).strftime("%Y-%m-%dT00:00:00Z")

    q = f"""
SELECT ?player ?playerLabel ?birth ?countryLabel ?ig WHERE {{
  ?player wdt:P106 wd:Q937857 .
  ?player p:P54 ?teamStmt .
  ?teamStmt ps:P54 wd:{team_qid} .
  OPTIONAL {{ ?teamStmt pq:P582 ?end . }}
  FILTER(!BOUND(?end) || ?end >= "{season_start_dt}"^^xsd:dateTime)
  OPTIONAL {{ ?player wdt:P569 ?birth . }}
  OPTIONAL {{ ?player wdt:P27 ?country . }}
  OPTIONAL {{ ?player wdt:P2002 ?ig . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en". }}
}}
LIMIT {int(limit)}
"""
    data = wikidata_sparql(q)
    out = []
    seen = set()
    for b in data.get("results", {}).get("bindings", []):
        name = (b.get("playerLabel", {}).get("value") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "name": name,
                "birth_date": (b.get("birth", {}).get("value") or "")[:10] or None,
                "nationality": b.get("countryLabel", {}).get("value"),
                "instagram": normalize_instagram_handle(b.get("ig", {}).get("value")),
            }
        )
    return out

def wikidata_claim_string(entity: dict, pid: str) -> Optional[str]:
    try:
        claims = entity["claims"].get(pid)
        if not claims:
            return None
        mainsnak = claims[0]["mainsnak"]
        datavalue = mainsnak.get("datavalue", {})
        val = datavalue.get("value")
        if isinstance(val, dict) and "time" in val:
            # +1998-11-04T00:00:00Z
            t = val["time"]
            return t[1:11]
        if isinstance(val, str):
            return val
        return None
    except Exception:
        return None


def wikipedia_extract_player_data(name: str) -> dict:
    """
    Best-effort Wikipedia (fr) parse: birth date + nationality from infobox/lead.
    Returns partial dict with keys birth_date, nationality, bio.
    """
    # Resolve a reliable page title first (prevents many 404 due to exact-title mismatch).
    search_url = (
        "https://fr.wikipedia.org/w/api.php?"
        f"action=query&list=search&srsearch={quote_plus(name)}&srlimit=1&format=json"
    )

    def _search():
        r = http_get(search_url)
        r.raise_for_status()
        data = r.json()
        hit = (((data or {}).get("query") or {}).get("search") or [])
        if not hit:
            return None
        return hit[0].get("title")

    try:
        title = RETRY.run(f"Wikipedia search {name}", _search)
    except Exception as e:
        append_log(f"Wikipedia search échoué ({name}): {e}")
        title = None

    if not title:
        # No Wikipedia page found (not an error; just missing).
        return {"birth_date": None, "nationality": None, "bio": None}

    # Use MediaWiki extracts API on resolved title (more robust than REST summary 404 cases).
    extract_url = (
        "https://fr.wikipedia.org/w/api.php?"
        f"action=query&prop=extracts&exintro=1&explaintext=1&redirects=1&titles={quote_plus(title)}&format=json"
    )

    def _do():
        r = http_get(extract_url)
        r.raise_for_status()
        return r.json()

    out = {"birth_date": None, "nationality": None, "bio": None}
    data = RETRY.run(f"Wikipedia extract {title}", _do)
    try:
        pages = ((data or {}).get("query") or {}).get("pages") or {}
        first_page = next(iter(pages.values())) if pages else {}
        extract = (first_page or {}).get("extract") or ""
        out["bio"] = extract.strip() or None
    except Exception:
        out["bio"] = None
    return out


def normalize_instagram_handle(handle_or_url: Optional[str]) -> Optional[str]:
    if not handle_or_url:
        return None
    s = handle_or_url.strip()
    lower = s.lower()
    if "instagram.com/" in lower:
        # Robust extraction even when URL includes tracking tokens (&sa=..., encoded fragments, etc.).
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)/?", s, re.IGNORECASE)
        if not m:
            return None
        s = m.group(1)
    s = s.split("?", 1)[0].split("#", 1)[0].split("&", 1)[0].strip("/")
    # Keep only profile handles (single path segment).
    if "/" in s or " " in s:
        return None
    if not s:
        return None
    blocked = {"p", "reel", "explore", "accounts", "stories", "popular"}
    if s.lower() in blocked:
        return None
    if s.startswith("@"):
        return s
    return f"@{s}"


def sanitize_filename(text: str) -> str:
    safe = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in text.strip().lower())
    safe = "_".join(part for part in safe.split("_") if part)
    return safe[:60] if safe else "recherche"


def generate_search_csv_path(query: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    query_part = sanitize_filename(query)
    return os.path.join(ROOT_DIR, f"{query_part}_{stamp}.csv")


def generate_export_filename(search_text: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = sanitize_filename(search_text or "export")
    return f"{base}_{stamp}.csv"


def ensure_excel_template() -> Optional[str]:
    """
    Create an Excel template near the example CSV if missing.
    Returns template path when available, else None.
    """
    ensure_app_folders()
    if os.path.exists(XLSX_TEMPLATE_PATH):
        return XLSX_TEMPLATE_PATH
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except Exception:
        return None
    try:
        wb = Workbook()
        ws = wb.active
        ws.title = "Sportifs"
        ws.append(CSV_EXPORT_COLUMNS)

        header_fill = PatternFill(fill_type="solid", fgColor="1F4E78")
        header_font = Font(color="FFFFFF", bold=True)
        for idx, col in enumerate(CSV_EXPORT_COLUMNS, start=1):
            c = ws.cell(row=1, column=idx, value=col)
            c.fill = header_fill
            c.font = header_font
            c.alignment = Alignment(horizontal="center", vertical="center")

        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{chr(ord('A') + len(CSV_EXPORT_COLUMNS) - 1)}1"

        widths = {
            "A": 18, "B": 16, "C": 14, "D": 12, "E": 16, "F": 14,
            "G": 24, "H": 18, "I": 24, "J": 16, "K": 14, "L": 18,
            "M": 16, "N": 14, "O": 28, "P": 28, "Q": 14, "R": 10,
        }
        for col, w in widths.items():
            ws.column_dimensions[col].width = w

        wb.save(XLSX_TEMPLATE_PATH)
        return XLSX_TEMPLATE_PATH
    except Exception:
        return None


def ensure_instaloader():
    # instaloader removed (not in allowed dependencies). Keep function for compatibility.
    return None


def ensure_selenium():
    global webdriver, WebDriverException, By
    if webdriver is None or By is None:
        try:
            from selenium import webdriver as _webdriver
            from selenium.common.exceptions import WebDriverException as _WebDriverException
            from selenium.webdriver.common.by import By as _By
            webdriver = _webdriver
            WebDriverException = _WebDriverException
            By = _By
        except Exception:
            return None, Exception, None
    return webdriver, WebDriverException, By


class SplashScreen:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.win = tk.Toplevel(root)
        self.win.title(APP_TITLE)
        self.win.resizable(False, False)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)

        frm = ttk.Frame(self.win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=APP_TITLE, font=("Segoe UI", 14, "bold")).pack(anchor="w")
        self.msg = tk.StringVar(value="Démarrage…")
        ttk.Label(frm, textvariable=self.msg).pack(anchor="w", pady=(8, 10))
        self.bar = ttk.Progressbar(frm, mode="indeterminate", length=360)
        self.bar.pack(fill="x")

        self._center()

    def _center(self) -> None:
        self.win.update_idletasks()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def show(self, message: str) -> None:
        self.msg.set(message)
        self.bar.start(10)
        self.win.update_idletasks()

    def close(self) -> None:
        try:
            self.bar.stop()
            self.win.destroy()
        except Exception:
            pass


class SearchProgressDialog:
    def __init__(self, root: tk.Tk, total: int, on_cancel) -> None:
        self.root = root
        self.total = max(1, int(total))
        self.indeterminate = True
        self._on_cancel = on_cancel
        self.win = tk.Toplevel(root)
        self.win.title("Recherche en cours…")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        frm = ttk.Frame(self.win, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Recherche en cours…", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.details = tk.StringVar(value="Initialisation…")
        ttk.Label(frm, textvariable=self.details, wraplength=520).pack(anchor="w", pady=(8, 10))

        self.bar = ttk.Progressbar(frm, mode="indeterminate", length=520, maximum=self.total)
        self.bar.pack(fill="x")
        self.bar.start(12)

        self.counter = tk.StringVar(value="Analysés: 0 | Retenus: 0")
        ttk.Label(frm, textvariable=self.counter).pack(anchor="w", pady=(6, 0))

        actions = ttk.Frame(frm)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Annuler", command=self._cancel).pack(side="right")

        self._center()
        self.win.transient(root)
        # Do not grab: allow moving the main window during search.

    def _cancel(self) -> None:
        try:
            self.details.set("Annulation en cours…")
            self.win.update_idletasks()
        except Exception:
            pass
        try:
            if callable(self._on_cancel):
                self._on_cancel()
        except Exception:
            pass

    def _center(self) -> None:
        self.win.update_idletasks()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def set_determinate(self, total: Optional[int] = None) -> None:
        if total is not None and total > 0:
            self.total = int(total)
        if self.indeterminate:
            self.bar.stop()
            self.bar.configure(mode="determinate", maximum=self.total, value=0)
            self.indeterminate = False

    def set_indeterminate(self) -> None:
        if not self.indeterminate:
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)
            self.indeterminate = True

    def update(self, current: int, message: str, analyzed: int = 0, retained: int = 0) -> None:
        c = max(0, min(int(current), self.total))
        self.details.set(message)
        if not self.indeterminate:
            self.bar.configure(value=c)
        self.counter.set(f"Analysés: {analyzed} | Retenus: {retained}")
        self.win.update_idletasks()

    def close(self) -> None:
        try:
            self.bar.stop()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


class BusyDialog:
    def __init__(self, root: tk.Tk, title: str, message: str) -> None:
        self.win = tk.Toplevel(root)
        self.win.title(title)
        self.win.resizable(False, False)
        self.win.transient(root)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)
        # Do not grab: allow moving the main window during background work.

        frm = ttk.Frame(self.win, padding=14)
        frm.pack(fill="both", expand=True)
        self.message_var = tk.StringVar(value=message)
        ttk.Label(frm, textvariable=self.message_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))
        self.bar = ttk.Progressbar(frm, mode="indeterminate", length=360)
        self.bar.pack(fill="x")

        self._center()
        self.bar.start(12)
        self.win.update_idletasks()

    def update_message(self, message: str) -> None:
        self.message_var.set(message)
        self.win.update_idletasks()

    def _center(self) -> None:
        self.win.update_idletasks()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        x = int((sw - w) / 2)
        y = int((sh - h) / 2)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    def close(self) -> None:
        try:
            self.bar.stop()
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass


@dataclass
class SearchFilters:
    sport: str
    club: str
    ville: str
    saison: str
    saison_start_year: Optional[int]
    # No practical cap by default.
    max_profiles: int = 1_000_000
    # Football : quel effectif cibler sur le site officiel (Google/DDGS).
    roster_equipe: str = "masculin"  # "masculin" | "feminin"

    @property
    def query(self) -> str:
        return " ".join(x for x in [self.sport, self.club, self.ville, self.saison] if x).strip()


class AthleteApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        ensure_app_folders()
        self.withdraw()

        self.title(APP_TITLE)
        self.geometry("1450x720")
        self.minsize(1200, 640)

        self.current_csv_path: Optional[str] = None
        self.final_results: List[dict] = []
        self._search_dialog: Optional[SearchProgressDialog] = None
        self._search_progress_current = 0
        self._search_progress_analyzed = 0
        self._search_progress_retained = 0
        self._search_cancel_event: Optional[threading.Event] = None
        self._search_cancel_requested = False
        self._search_in_progress = False
        self._autosave_dirty_during_search = False
        self._active_busy_dialog: Optional[BusyDialog] = None
        self._instagram_prompt_done = False
        self._instagram_public_ok: Optional[bool] = None
        self._instagram_public_message: str = "Instagram: initialisation…"
        self._ig_mode_var = tk.StringVar(value="Rapide" if IG_FAST_MODE_DEFAULT else "Complet")
        self._ig_fast_mode_current = bool(IG_FAST_MODE_DEFAULT)
        # Abonnés / posts extraits de l’extrait SERP (DDG/Google) sans appeler le profil Instagram.
        self._pending_serp_ig_stats: Optional[Dict[str, Optional[int]]] = None
        self._ig_scraper = None
        if InstagramScraper is not None:
            try:
                ig_user = (os.getenv("IG_USER", "") or "").strip() or IG_DEFAULT_USER
                ig_pass = (os.getenv("IG_PASS", "") or "").strip() or IG_DEFAULT_PASS
                self._ig_scraper = InstagramScraper(
                    allow_selenium_fallback=True,
                    logger_fn=append_log,
                    auth_username=ig_user,
                    auth_password=ig_pass,
                )
            except Exception:
                self._ig_scraper = None
        self._splash_started_at = dt.datetime.now()
        self._splash = SplashScreen(self)
        self._splash.show("Chargement de l'interface…")
        self.after(50, self._finish_startup)

    def _finish_startup(self) -> None:
        self._build_ui()
        self._set_status("Initialisation…")
        self._start_background_checks()
        self._set_status("Prêt.")

        elapsed_ms = int((dt.datetime.now() - self._splash_started_at).total_seconds() * 1000)
        remaining_ms = max(0, MIN_SPLASH_MS - elapsed_ms)
        self.after(remaining_ms, self._close_startup_splash)

    def _close_startup_splash(self) -> None:
        if getattr(self, "_splash", None) is not None:
            self._splash.close()
        self.deiconify()
        # Instagram init via instaloader removed; keep status neutral.
        self._set_status("Prêt.")
        # One-shot restore of active CSV after an app update relaunch.
        self.after(120, self._restore_pending_csv_after_update)

    def _save_pending_csv_for_update(self) -> None:
        try:
            path = (self.current_csv_path or "").strip()
            if not path or not os.path.exists(path):
                if os.path.exists(PENDING_CSV_PATH):
                    os.remove(PENDING_CSV_PATH)
                return
            with open(PENDING_CSV_PATH, "w", encoding="utf-8") as f:
                f.write(path)
        except Exception:
            pass

    def _restore_pending_csv_after_update(self) -> None:
        try:
            if not os.path.exists(PENDING_CSV_PATH):
                return
            with open(PENDING_CSV_PATH, "r", encoding="utf-8") as f:
                path = (f.read() or "").strip()
            try:
                os.remove(PENDING_CSV_PATH)
            except Exception:
                pass
            if not path or not os.path.exists(path):
                return
            self._load_csv(path)
            self.current_csv_path = path
            self._set_status(f"CSV restauré après mise à jour: {path}")
        except Exception:
            pass

    def _start_background_checks(self) -> None:
        t = threading.Thread(target=self._check_chrome_setup_non_blocking, daemon=True)
        t.start()
        t2 = threading.Thread(target=self._init_instagram_public_non_blocking, daemon=True)
        t2.start()

    def _init_instagram_public_non_blocking(self) -> None:
        """
        Initialize Instagram access at startup (auth if possible, else public).
        This updates the right-side status and allows the user to re-run via button.
        """
        try:
            self.after(0, lambda: self._set_status("Initialisation Instagram (auth/public)…"))
            append_log(
                f"[IG] mode résolution handles: {'rapide (DDG)' if self._ig_ui_is_fast_mode() else 'complet (Google+DDG+Selenium)'}"
            )
            ok = False
            if self._ig_scraper is not None:
                if self._ig_scraper.has_auth_credentials():
                    ok = bool(self._ig_scraper._ensure_auth_client())
                    if ok:
                        self._instagram_public_message = "Instagram: OK (auth)"
                if not ok:
                    ok = bool(self._ig_scraper.probe())
            else:
                # Probe a stable public profile page (legacy lightweight probe)
                probe = instagram_public_scrape("https://www.instagram.com/instagram")
                ok = (probe.get("followers") is not None) or (probe.get("posts") is not None) or bool(probe.get("bio"))
            self._instagram_public_ok = bool(ok)
            if self._instagram_public_ok and not self._instagram_public_message.endswith("(auth)"):
                self._instagram_public_message = "Instagram: OK (public)"
            else:
                if not self._instagram_public_message.endswith("(auth)"):
                    self._instagram_public_message = "Instagram: limité (public)"
        except Exception:
            self._instagram_public_ok = False
            self._instagram_public_message = "Instagram: limité (public)"
        finally:
            self.after(0, lambda: self._update_instagram_status(bool(self._instagram_public_ok)))

    def _check_chrome_setup_non_blocking(self) -> None:
        webdrv, webdrv_exc, _ = ensure_selenium()
        if webdrv is None:
            return
        try:
            options = webdrv.ChromeOptions()
            options.add_argument("--headless=new")
            driver = webdrv.Chrome(options=options)
            driver.quit()
        except (webdrv_exc, ModuleNotFoundError, ImportError):
            self.after(
                0,
                lambda: messagebox.showwarning(
                    APP_TITLE,
                    "Module Selenium/Chrome indisponible.\n"
                    "Relancez le build (onedir) et installez Google Chrome si nécessaire.",
                ),
            )

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        form_row = ttk.Frame(top)
        form_row.pack(fill="x")

        actions_row = ttk.Frame(top)
        actions_row.pack(fill="x", pady=(6, 0))

        # (Info row removed) status line will show Instagram status.

        ttk.Label(form_row, text="Sport *").grid(row=0, column=0, sticky="w")
        self.sport_var = tk.StringVar(value=DEFAULT_SPORT)
        # Largeur initiale modeste, mais la colonne s'étire avec la fenêtre.
        self.sport_entry = ttk.Entry(form_row, textvariable=self.sport_var, width=12)
        self.sport_entry.grid(row=0, column=1, padx=6, sticky="ew")
        self.sport_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(form_row, text="Club").grid(row=0, column=2, sticky="w")
        self.club_var = tk.StringVar(value=DEFAULT_CLUB)
        self.club_entry = ttk.Entry(form_row, textvariable=self.club_var, width=14)
        self.club_entry.grid(row=0, column=3, padx=6, sticky="ew")
        self.club_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(form_row, text="Ville").grid(row=0, column=4, sticky="w")
        self.ville_var = tk.StringVar(value=DEFAULT_VILLE)
        self.ville_entry = ttk.Entry(form_row, textvariable=self.ville_var, width=12)
        self.ville_entry.grid(row=0, column=5, padx=6, sticky="ew")
        self.ville_entry.bind("<Return>", lambda _e: self.on_search())

        self.roster_equipe_var = tk.StringVar(value="Masculin")
        self.roster_equipe_combo = ttk.Combobox(
            form_row,
            textvariable=self.roster_equipe_var,
            values=("Masculin", "Féminin"),
            state="readonly",
            width=11,
        )
        self.roster_equipe_combo.grid(row=0, column=6, sticky="w", padx=(0, 8))

        ttk.Label(form_row, text="Saison").grid(row=0, column=7, sticky="w")
        self.saison_var = tk.StringVar(value=default_season_label())
        self.saison_entry = ttk.Entry(form_row, textvariable=self.saison_var, width=12)
        self.saison_entry.grid(row=0, column=8, padx=6, sticky="w")
        self.saison_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(form_row, text="Max joueurs:").grid(row=0, column=9, sticky="e")
        self.max_profiles_var = tk.StringVar(value="20")
        ttk.Entry(form_row, textvariable=self.max_profiles_var, width=8).grid(row=0, column=10, padx=6, sticky="w")

        self.search_btn = ttk.Button(actions_row, text="🔍", width=3, command=self.on_search)
        self.search_btn.grid(row=0, column=0, padx=(0, 8), sticky="w")

        # Bouton Instagram supprimé: l'init se fait automatiquement au lancement (statut à droite).
        ttk.Button(actions_row, text="Charger CSV", command=self.on_load_csv).grid(row=0, column=2, padx=4, sticky="w")
        ttk.Button(actions_row, text="Exporter en CSV", command=self.on_export_csv).grid(row=0, column=3, padx=4, sticky="w")
        ttk.Button(actions_row, text="Exporter Excel (template)", command=self.on_export_excel).grid(row=0, column=4, padx=4, sticky="w")
        row_actions = ttk.Frame(actions_row)
        row_actions.grid(row=0, column=5, padx=(0, 4), sticky="w")
        ttk.Button(row_actions, text="+", width=2, command=self.on_add_manual).pack(side="left", padx=(0, 2))
        ttk.Button(row_actions, text="−", width=2, command=self.on_delete_selected_rows).pack(side="left")
        # Stretch spacer so logs/IG/update controls stay aligned on the far right.
        actions_row.columnconfigure(6, weight=1)
        self.toggle_logs_btn = ttk.Button(actions_row, text="Afficher logs", command=self._toggle_logs)
        self.toggle_logs_btn.grid(row=0, column=7, padx=10, sticky="e")
        self._ig_mode_combo = ttk.Combobox(
            actions_row,
            textvariable=self._ig_mode_var,
            values=("Rapide", "Complet"),
            state="readonly",
            width=10,
        )
        self._ig_mode_combo.grid(row=0, column=8, padx=6, sticky="e")

        ttk.Button(actions_row, text="Mise à jour", command=self.on_update_app).grid(
            row=0, column=9, padx=6, sticky="e"
        )

        # Champs sport/club/ville dynamiques: les colonnes 1/3/5 s'étirent avec la fenêtre.
        form_row.columnconfigure(1, weight=2, minsize=120)
        form_row.columnconfigure(3, weight=3, minsize=160)
        form_row.columnconfigure(5, weight=2, minsize=120)
        # Colonnes saison / max joueurs gardent une taille compacte.
        form_row.columnconfigure(8, weight=0, minsize=80)

        status_frame = ttk.Frame(self, padding=(10, 0, 10, 5))
        status_frame.pack(fill="x")
        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")

        self.ig_status_var = tk.StringVar(value="Instagram: Web-only")
        self.ig_status_label = ttk.Label(status_frame, textvariable=self.ig_status_var, anchor="e")
        self.ig_status_label.pack(side="right")
        self.ig_status_label.configure(foreground="#2a4b8d")

        # Logs panel (scrollable + hide/show)
        self.logs_visible = False
        self.logs_frame = ttk.Frame(self, padding=(10, 0, 10, 6))
        self.logs_text = tk.Text(self.logs_frame, height=10, wrap="word")
        self.logs_scroll = ttk.Scrollbar(self.logs_frame, orient="vertical", command=self.logs_text.yview)
        self.logs_text.configure(yscrollcommand=self.logs_scroll.set)
        self.logs_text.grid(row=0, column=0, sticky="nsew")
        self.logs_scroll.grid(row=0, column=1, sticky="ns")
        self.logs_frame.columnconfigure(0, weight=1)
        self.logs_frame.rowconfigure(0, weight=1)
        self.logs_text.configure(state="disabled")

        # Connect global logger sink to this UI instance.
        global LOG_SINK
        LOG_SINK = self._append_log_ui_threadsafe

        table_wrap = ttk.Frame(self, padding=(10, 0, 10, 10))
        table_wrap.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(table_wrap, columns=CSV_COLUMNS, show="headings")
        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_wrap.rowconfigure(0, weight=1)
        table_wrap.columnconfigure(0, weight=1)

        heading_font = tkfont.nametofont("TkHeadingFont")
        last_col = CSV_COLUMNS[-1]
        for col in CSV_COLUMNS:
            self.tree.heading(col, text=col)
            # Size columns from their header text length for a cleaner default layout.
            width = max(90, heading_font.measure(col) + 28)
            # stretch=False sur les colonnes : largeur ajustable au glissement entre en-têtes (type Excel). La dernière colonne absorbe l’élargissement de la fenêtre.
            self.tree.column(
                col,
                width=width,
                anchor="w",
                stretch=(col == last_col),
                minwidth=50,
            )

        self.tree.configure(selectmode="extended", takefocus=True)
        self.tree.bind("<Double-1>", self._edit_cell)
        self.tree.bind("<ButtonRelease-1>", self._maybe_open_instagram)
        self.tree.bind("<Delete>", lambda _e: self.on_delete_selected_rows())
        self.tree.bind("<Control-a>", self._on_tree_select_all)
        self.tree.bind("<Control-A>", self._on_tree_select_all)
        if platform.system() == "Darwin":
            self.tree.bind("<Command-a>", self._on_tree_select_all)
            self.tree.bind("<Command-A>", self._on_tree_select_all)

    def _toggle_logs(self) -> None:
        self.logs_visible = not self.logs_visible
        if self.logs_visible:
            self.logs_frame.pack(fill="both", expand=False)
            self.toggle_logs_btn.configure(text="Masquer logs")
        else:
            self.logs_frame.pack_forget()
            self.toggle_logs_btn.configure(text="Afficher logs")

    def _append_log_ui_threadsafe(self, text: str) -> None:
        # Called from any thread
        self.after(0, lambda t=text: self._append_log_ui(t))

    def _append_log_ui(self, text: str) -> None:
        try:
            self.logs_text.configure(state="normal")
            self.logs_text.insert("end", text + "\n")
            # Keep last ~2000 lines
            lines = int(self.logs_text.index("end-1c").split(".")[0])
            if lines > 2000:
                self.logs_text.delete("1.0", f"{lines-2000}.0")
            self.logs_text.see("end")
            self.logs_text.configure(state="disabled")
        except Exception:
            pass

    def _set_status(self, text: str, *, step: bool = False) -> None:
        self.status_var.set(text)
        append_log(text, step=step)
        # Mirror status messages inside search popup so important steps stay visible.
        if self._search_dialog is not None:
            self._search_dialog.update(
                self._search_progress_current,
                text,
                analyzed=self._search_progress_analyzed,
                retained=self._search_progress_retained,
            )
        # Mirror status messages inside active busy popup (startup/login).
        if self._active_busy_dialog is not None:
            self._active_busy_dialog.update_message(text)
        self.update_idletasks()

    def _current_search_text(self) -> str:
        return " ".join(
            x
            for x in [
                self.sport_var.get().strip(),
                self.club_var.get().strip(),
                self.ville_var.get().strip(),
                getattr(self, "saison_var", tk.StringVar(value="")).get().strip(),
            ]
            if x
        ).strip()

    def _update_instagram_status(self, connected: bool) -> None:
        # Public-mode status (no login). `connected` means "public access seems OK".
        msg = self._instagram_public_message if getattr(self, "_instagram_public_message", None) else "Instagram: public"
        self.ig_status_var.set(msg)
        if connected:
            self.ig_status_label.configure(foreground="#0f766e")  # green
        else:
            self.ig_status_label.configure(foreground="#b45309")  # amber

    def _get_filters(self) -> Optional[SearchFilters]:
        sport = self.sport_var.get().strip()
        club = self.club_var.get().strip()
        ville = self.ville_var.get().strip()
        saison = (self.saison_var.get().strip() if getattr(self, "saison_var", None) is not None else "").strip()
        saison_start_year = parse_season_start_year(saison) if saison else None
        if not sport:
            messagebox.showwarning(APP_TITLE, "Le champ Sport est obligatoire.")
            return None
        try:
            max_profiles_text = (self.max_profiles_var.get().strip() if getattr(self, "max_profiles_var", None) else "")
            max_profiles = int(max_profiles_text) if max_profiles_text else 1_000_000
            if max_profiles <= 0:
                raise ValueError("max_profiles<=0")
        except ValueError:
            messagebox.showerror(APP_TITLE, "Filtres invalides. Utilisez des nombres.")
            return None
        raw_eq = (self.roster_equipe_var.get() or "Masculin").strip().lower()
        roster_equipe = "feminin" if raw_eq.startswith("femin") else "masculin"
        return SearchFilters(
            sport=sport,
            club=club,
            ville=ville,
            saison=saison,
            saison_start_year=saison_start_year,
            max_profiles=max_profiles,
            roster_equipe=roster_equipe,
        )

    def _ig_ui_is_fast_mode(self) -> bool:
        """True si le menu IG est sur « Rapide » (DDG-first, pas de scraping profil)."""
        v = (self._ig_mode_var.get() or "").strip().lower()
        return v.startswith("r")

    def on_search(self) -> None:
        filters = self._get_filters()
        if not filters:
            return
        start_search_log_timing()
        self._search_in_progress = True
        self._autosave_dirty_during_search = False
        # Freeze IG mode for the whole search run to avoid mid-run toggles/inconsistent logs.
        self._ig_fast_mode_current = self._ig_ui_is_fast_mode()
        append_log(
            f"[IG] mode recherche courant: {'rapide (DDG)' if self._ig_fast_mode_current else 'complet (Google+DDG+Selenium)'}",
            step=True,
        )
        if filters.saison.strip():
            y = filters.saison_start_year
            append_log(
                f"Saison « {filters.saison.strip()} » → "
                f"{'année début ' + str(y) + ' (Wikipédia strict + filtre Wikidata si SPARQL)' if y is not None else 'année non reconnue — saisir ex. 2025-2026'}",
                step=True,
            )
        if self._ig_scraper is not None:
            try:
                self._ig_scraper.set_fast_mode(self._ig_fast_mode_current)
            except Exception:
                pass
        # New cancel token per search
        self._search_cancel_event = threading.Event()
        self._search_cancel_requested = False
        self.search_btn.configure(state="disabled")
        self._set_status("Recherche en cours...", step=True)
        if self._search_dialog is not None:
            self._search_dialog.close()
        self._search_progress_current = 0
        self._search_progress_analyzed = 0
        self._search_progress_retained = 0
        self._search_dialog = SearchProgressDialog(self, total=filters.max_profiles, on_cancel=self._cancel_current_search)
        self._search_dialog.set_indeterminate()
        self._search_dialog.update(0, "Initialisation de la recherche…", analyzed=0, retained=0)
        t = threading.Thread(target=self._run_search, args=(filters,), daemon=True)
        t.start()

    def _cancel_current_search(self) -> None:
        if self._search_cancel_event is not None:
            self._search_cancel_event.set()
        if not self._search_cancel_requested:
            append_log("Annulation demandée par l'utilisateur.", step=True)
            self._search_cancel_requested = True

    def _raise_if_cancelled(self) -> None:
        if self._search_cancel_event is not None and self._search_cancel_event.is_set():
            raise SearchCancelled("Recherche annulée.")

    def _sleep_with_cancel(self, seconds: float) -> None:
        end = time.time() + max(0.0, float(seconds))
        while time.time() < end:
            self._raise_if_cancelled()
            time.sleep(0.1)

    def _run_search(self, filters: SearchFilters) -> None:
        try:
            bind_search_cancel_event(self._search_cancel_event)
            self._raise_if_cancelled()
            rows = self._search_players_then_instagram(filters)
            self._raise_if_cancelled()
            if not rows:
                # Wikidata fallback (structured) when web parsing yields 0
                rows = self._search_wikidata_roster(filters)
            self._raise_if_cancelled()
            if not rows:
                self.after(
                    0,
                    lambda: self._set_status(
                        "Aucun profil trouvé. Précisez Club / Ville ou la saison.",
                        step=True,
                    ),
                )
            else:
                self.after(0, lambda: self._set_status(f"{len(rows)} profils trouvés.", step=True))
                self.after(0, lambda: self._autosave_csv(filters.query, timing_step=True))
                self.after(0, lambda: self._build_final_results_from_table())
        except SearchCancelled:
            self.after(0, lambda: self._set_status("Recherche annulée.", step=True))
            self.after(0, lambda: self._autosave_csv(filters.query, timing_step=True))
            self.after(0, lambda: self._build_final_results_from_table())
        except Exception as exc:
            append_log(traceback.format_exc())
            self.after(0, lambda: messagebox.showerror(APP_TITLE, self._format_user_error(exc)))
        finally:
            bind_search_cancel_event(None)
            # Après les autres after(0) du try/except (statut, autosave) pour garder le chrono actif.
            self.after(0, self._close_search_dialog)
            self.after(0, lambda: self.search_btn.configure(state="normal"))
            # Si l'utilisateur a modifié une ligne pendant la recherche, on persiste une fois à la fin.
            if self._autosave_dirty_during_search:
                self.after(0, lambda: self._autosave_csv(self._current_search_text() or "manuel", timing_step=True))
                self.after(0, lambda: self._build_final_results_from_table())
                self._autosave_dirty_during_search = False
            self._search_in_progress = False
            self.after(0, stop_search_log_timing)

    def _search_wikidata_roster(self, filters: SearchFilters) -> List[Dict[str, str]]:
        club = (filters.club or "").strip()
        ville = (filters.ville or "").strip()
        if not club:
            return []

        try:
            if filters.saison_start_year is not None:
                self.after(
                    0,
                    lambda y=filters.saison_start_year: self._set_status(
                        f"Wikidata: récupération de la liste joueurs (saison {y}-{y+1})…",
                        step=True,
                    ),
                )
            else:
                self.after(
                    0,
                    lambda: self._set_status(
                        "Wikidata: récupération de la liste joueurs (période récente)…",
                        step=True,
                    ),
                )
            players = wikidata_team_players(
                club,
                limit=filters.max_profiles * 3,
                season_start_year=filters.saison_start_year,
            )
            append_log(f"Wikidata: {len(players)} joueurs candidats reçus.", step=True)
        except Exception as e:
            append_log(f"Wikidata roster échoué: {e}", step=True)
            # Fallback when SPARQL is down: try extract names from Wikipedia squad/season pages.
            try:
                self.after(
                    0,
                    lambda: self._set_status("Fallback: Wikipedia (fr, page effectif/saison)…", step=True),
                )
                season_text = filters.saison or ""
                # Club alias to improve Wikipedia target (psg -> full club name).
                club_for_wiki = club.strip()
                if club_for_wiki.lower() in ("psg", "paris sg", "paris-sg"):
                    club_for_wiki = "Paris Saint-Germain F.C."

                # Prefer an exact season page title search (reduces “historical players” pages).
                start = filters.saison_start_year
                title_fr = None
                if start is not None:
                    end = start + 1
                    for lab in (f"{start}–{end}", f"{start}-{end}"):
                        title_fr = wikipedia_find_page_title(f"Saison {lab} {club_for_wiki}", lang="fr")
                        if title_fr:
                            break
                        title_fr = wikipedia_find_page_title(f"{lab} {club_for_wiki}", lang="fr")
                        if title_fr:
                            break

                if not title_fr:
                    title_fr = wikipedia_find_page_title(f"{club_for_wiki} effectif", lang="fr")
                if not title_fr:
                    q_parts = [club_for_wiki, "effectif", season_text]
                    search_query = " ".join(x for x in q_parts if x).strip()
                    title_fr = wikipedia_find_page_title(search_query, lang="fr")
                if not title_fr:
                    wiki_sp = (filters.sport or "").strip()
                    if wiki_sp:
                        title_fr = wikipedia_find_page_title(f"{club_for_wiki} {wiki_sp}", lang="fr")
                    if not title_fr:
                        title_fr = wikipedia_find_page_title(f"{club_for_wiki} équipe", lang="fr")
                if not title_fr:
                    title_fr = wikipedia_find_page_title(club_for_wiki, lang="fr")

                title_en = None
                names: List[str] = []
                strict_fb = filters.saison_start_year is not None
                if title_fr:
                    append_log(f"Fallback Wikipedia (fr): page = {title_fr}", step=True)
                    names = wikipedia_extract_names_from_page(
                        title_fr,
                        limit=filters.max_profiles * 4,
                        section_hints=WIKI_FR_ROSTER_SECTIONS_STRICT,
                        lang="fr",
                        strict_current_squad=strict_fb,
                    )
                    if not names and strict_fb:
                        append_log("Fallback Wikipedia (fr): 2e passe (sections élargies)", step=True)
                        names = wikipedia_extract_names_from_page(
                            title_fr,
                            limit=filters.max_profiles * 4,
                            section_hints=WIKI_FR_ROSTER_SECTIONS_LOOSE,
                            lang="fr",
                            strict_current_squad=False,
                        )
                    append_log(f"Fallback Wikipedia (fr): {len(names)} noms extraits.", step=True)
                if not names:
                    if start is not None:
                        end = start + 1
                        for lab in (f"{start}–{end}", f"{start}-{end}"):
                            title_en = wikipedia_find_page_title(f"{lab} {club_for_wiki} season", lang="en")
                            if title_en:
                                break
                    if not title_en:
                        title_en = wikipedia_find_page_title(
                            f"{club_for_wiki} current squad {season_text}".strip(), lang="en"
                        )
                    if not title_en:
                        title_en = wikipedia_find_page_title(f"{club_for_wiki} squad".strip(), lang="en")
                    if title_en:
                        append_log(f"Fallback Wikipedia (en): page = {title_en}", step=True)
                        names = wikipedia_extract_names_from_page(
                            title_en,
                            limit=filters.max_profiles * 4,
                            section_hints=["Current squad", "Squad"],
                            lang="en",
                            strict_current_squad=strict_fb,
                        )
                        if not names and strict_fb:
                            names = wikipedia_extract_names_from_page(
                                title_en,
                                limit=filters.max_profiles * 4,
                                section_hints=["Current squad", "Squad", "Players"],
                                lang="en",
                                strict_current_squad=False,
                            )
                        append_log(f"Fallback Wikipedia (en): {len(names)} noms extraits.", step=True)
                if not names:
                    append_log("Fallback Wikipedia: aucune page exploitable ou aucun nom extrait.", step=True)
                    return []
                append_roster_preview_log("Effectif (recherche Wikidata → Wikipedia)", names)
                # Build rows similarly to web-first pipeline but without Selenium.
                rows: List[Dict[str, str]] = []
                for idx, name in enumerate(names, start=1):
                    self._raise_if_cancelled()
                    if len(rows) >= filters.max_profiles:
                        break
                    append_log(
                        f"--- Enrichissement profil {idx}/{min(len(names), filters.max_profiles)}: {name} (fallback Wikipedia) ---",
                        step=True,
                    )
                    row = {c: "" for c in CSV_COLUMNS}
                    nom, prenom = self._split_name(name)
                    row["Nom"] = nom
                    row["Prénom"] = prenom
                    row["Date d'ajout"] = now_str()
                    row["Sport"] = filters.sport
                    row["Club"] = club
                    row["Ville"] = ville
                    row["Autres informations"] = ""
                    # Enrich Wikipedia -> Wikidata (best-effort)
                    try:
                        ps = self._build_player_struct(
                            name=name,
                            ig_username=None,
                            club_hint=club,
                            sport_hint=filters.sport or "",
                            ville_hint=ville,
                        )
                        if ps.get("birth_date"):
                            row["Date de naissance"] = ps["birth_date"]
                            row["Age"] = calc_age_human(ps["birth_date"]) or str(ps.get("age") or "")
                        if ps.get("nationality"):
                            row["Nationalité"] = ps["nationality"]
                        if ps.get("bio"):
                            row["Info en bio"] = ps["bio"]
                        if ps.get("instagram"):
                            row["Instagram"] = ps["instagram"]
                    except Exception as ex2:
                        append_log(f"Fallback Wikipedia enrichissement échoué ({name}): {ex2}")

                    rows.append(row)
                    self.after(0, lambda rr=dict(row): self._add_rows([rr]))
                    self.after(
                        0,
                        lambda a=idx, r=len(rows), n=name: self._update_search_dialog(
                            a, f"Wikipedia: {n}", analyzed=a, retained=r
                        ),
                    )
                return rows
            except Exception as ex:
                append_log(f"Fallback Wikipedia échoué: {ex}")
                return []

        rows: List[Dict[str, str]] = []
        analyzed = 0
        for p in players:
            self._raise_if_cancelled()
            if len(rows) >= filters.max_profiles:
                break
            analyzed += 1
            name = p.get("name") or ""
            append_log(
                f"--- Enrichissement profil {analyzed}/{min(len(players), filters.max_profiles)}: {name} (Wikidata) ---",
                step=True,
            )
            ig = p.get("instagram")
            nom, prenom = self._split_name(name)
            row = {c: "" for c in CSV_COLUMNS}
            row["Nom"] = nom
            row["Prénom"] = prenom
            row["Date d'ajout"] = now_str()
            row["Sport"] = filters.sport
            row["Club"] = club
            row["Ville"] = ville
            row["Date de naissance"] = p.get("birth_date") or ""
            row["Age"] = calc_age_human(row["Date de naissance"]) if row["Date de naissance"] else ""
            row["Nationalité"] = p.get("nationality") or ""
            row["Instagram"] = (f"https://instagram.com/{ig[1:]}" if ig and ig.startswith("@") else (ig or ""))
            row["Info en bio"] = ""
            row["Autres informations"] = ""
            row["Nombre de points"] = ""
            row["Niveau Barème"] = ""
            row["Priorité"] = ""

            # Info en bio: Instagram uniquement (laisser vide si non trouvé).

            # Followers/posts via web scraping is best-effort; keep empty if fails.
            rows.append(row)
            self.after(0, lambda rr=dict(row): self._add_rows([rr]))
            self.after(
                0,
                lambda a=analyzed, r=len(rows), n=name: self._update_search_dialog(
                    a, f"Wikidata: {n}", analyzed=a, retained=r
                ),
            )

        return rows

    def _format_user_error(self, exc: Exception) -> str:
        msg = str(exc) if exc else "Erreur inconnue."
        # Enrichir les messages liés aux limites Instagram / fallback web.
        if "Recherche limitée par Instagram" in msg:
            return (
                "Instagram a limité l'accès automatique (rate limit / anti-bot / challenge).\n\n"
                "Ce que l'application fait ensuite:\n"
                "- Elle tente automatiquement le mode Web (recherche via moteur + liens Instagram).\n"
                "- Si Instagram reste bloqué, certaines colonnes (abonnés/posts/bio) peuvent rester vides.\n\n"
                "Actions conseillées:\n"
                "- Réessayer plus tard.\n"
                "- Utiliser un VPN.\n"
                "- Vérifier que Google Chrome est installé (fallback web Selenium).\n\n"
                f"Détail technique: {msg}"
            )
        if "Fallback web indisponible" in msg or "Selenium" in msg:
            return (
                "Le mode Web via Selenium n'est pas disponible sur ce poste.\n\n"
                "Causes probables:\n"
                "- Google Chrome n'est pas installé, ou Selenium n'est pas embarqué correctement.\n\n"
                "Actions:\n"
                "- L'application tente maintenant aussi un fallback Web HTTP (sans navigateur).\n"
                "- Si ça échoue encore: vérifier réseau/proxy/VPN.\n"
                "- Installer Google Chrome puis relancer le build (onedir) pour activer Selenium complet.\n\n"
                f"Détail technique: {msg}"
            )
        return f"Erreur: {msg}"

    def _search_players_then_instagram(self, filters: SearchFilters) -> List[Dict[str, str]]:
        sport = (filters.sport or "").strip()
        club = (filters.club or "").strip()
        ville = (filters.ville or "").strip()
        if not sport:
            append_log("Recherche web-first: sport vide, retour vide.", step=True)
            return []
        area = " ".join(x for x in [club, ville] if x).strip()
        if not area:
            area = sport

        # 0) Site officiel du club (Google « effectif » → DDGS → mapping optionnel), puis Wikipedia.
        player_names: List[str] = []
        roster_from_wikipedia = False
        prefer_fem = filters.roster_equipe == "feminin"
        if club:
            try:
                self.after(
                    0,
                    lambda: self._update_search_dialog(
                        0, "Recherche joueurs: site officiel du club…", analyzed=0, retained=0
                    ),
                )
                self._raise_if_cancelled()
                append_log(
                    "Effectif officiel: préférence équipe "
                    + ("féminine (IHM)" if prefer_fem else "masculine (IHM)"),
                    step=True,
                )
                roster_url = discover_official_roster_url_google_http(
                    club,
                    ville,
                    prefer_feminine=prefer_fem,
                    sport=sport,
                    season_start_year=filters.saison_start_year,
                )
                if not roster_url:
                    roster_url = discover_official_roster_url_ddgs(
                        club,
                        ville,
                        prefer_feminine=prefer_fem,
                        sport=sport,
                        season_start_year=filters.saison_start_year,
                    )
                if not roster_url:
                    roster_url = resolve_official_club_roster_url(club)
                if roster_url:
                    append_log(f"Site officiel effectif: {roster_url}", step=True)
                    player_names = official_site_extract_names_from_url(
                        roster_url, limit=max(8, filters.max_profiles * 4)
                    )
                    append_log(f"Site officiel: {len(player_names)} noms extraits.", step=True)
            except Exception as e:
                append_log(f"Site officiel effectif échoué: {e}", step=True)
                player_names = []

        # Fallback: Wikipedia (après échec site officiel) — français par défaut, anglais en secours si vide.
        try:
            if not player_names:
                self.after(
                    0,
                    lambda: self._update_search_dialog(
                        0, "Recherche joueurs: Wikipedia (fr, effectif/saison)…", analyzed=0, retained=0
                    ),
                )
                self._raise_if_cancelled()
                season_text = (filters.saison or "").strip()

                club_for_wiki = club.strip() or area
                if club_for_wiki.lower() in ("psg", "paris sg", "paris-sg"):
                    club_for_wiki = "Paris Saint-Germain F.C."

                start = filters.saison_start_year
                title = None
                if start is not None:
                    end = start + 1
                    for lab in (f"{start}–{end}", f"{start}-{end}"):
                        title = wikipedia_find_page_title(f"Saison {lab} {club_for_wiki}", lang="fr")
                        if title:
                            break
                        title = wikipedia_find_page_title(f"{lab} {club_for_wiki}", lang="fr")
                        if title:
                            break
                if not title:
                    title = wikipedia_find_page_title(f"{club_for_wiki} effectif", lang="fr")
                if not title:
                    wiki_sp = (sport or "").strip()
                    if wiki_sp:
                        title = wikipedia_find_page_title(f"{club_for_wiki} {wiki_sp}", lang="fr")
                    if not title:
                        title = wikipedia_find_page_title(f"{club_for_wiki} équipe", lang="fr")
                if not title:
                    title = wikipedia_find_page_title(club_for_wiki, lang="fr")

                strict_wiki = filters.saison_start_year is not None
                if title:
                    append_log(f"Recherche joueurs Wikipedia (fr): page = {title}", step=True)
                    player_names = wikipedia_extract_names_from_page(
                        title,
                        limit=filters.max_profiles * 4,
                        section_hints=WIKI_FR_ROSTER_SECTIONS_STRICT,
                        lang="fr",
                        strict_current_squad=strict_wiki,
                    )
                    if not player_names and strict_wiki:
                        append_log(
                            "Wikipedia (fr): extraction stricte (saison) vide, seconde passe sans filtre « effectif actuel »",
                            step=True,
                        )
                        player_names = wikipedia_extract_names_from_page(
                            title,
                            limit=filters.max_profiles * 4,
                            section_hints=WIKI_FR_ROSTER_SECTIONS_LOOSE,
                            lang="fr",
                            strict_current_squad=False,
                        )
                    append_log(f"Recherche joueurs Wikipedia (fr): {len(player_names)} noms extraits.", step=True)
                    if player_names:
                        roster_from_wikipedia = True

                if not player_names:
                    self.after(
                        0,
                        lambda: self._update_search_dialog(
                            0, "Recherche joueurs: Wikipedia (en, secours)…", analyzed=0, retained=0
                        ),
                    )
                    self._raise_if_cancelled()
                    title_en = None
                    if start is not None:
                        end = start + 1
                        for lab in (f"{start}–{end}", f"{start}-{end}"):
                            title_en = wikipedia_find_page_title(f"{lab} {club_for_wiki} season", lang="en")
                            if title_en:
                                break
                    if not title_en:
                        title_en = wikipedia_find_page_title(
                            f"{club_for_wiki} current squad {season_text}".strip(), lang="en"
                        )
                    if not title_en:
                        title_en = wikipedia_find_page_title(f"{club_for_wiki} squad".strip(), lang="en")

                    if title_en:
                        append_log(f"Recherche joueurs Wikipedia (en): page = {title_en}", step=True)
                        player_names = wikipedia_extract_names_from_page(
                            title_en,
                            limit=filters.max_profiles * 4,
                            section_hints=["Current squad", "Squad"],
                            lang="en",
                            strict_current_squad=strict_wiki,
                        )
                        if not player_names and strict_wiki:
                            append_log(
                                "Wikipedia (en): extraction stricte vide, seconde passe (sections élargies)",
                                step=True,
                            )
                            player_names = wikipedia_extract_names_from_page(
                                title_en,
                                limit=filters.max_profiles * 4,
                                section_hints=["Current squad", "Squad", "Players"],
                                lang="en",
                                strict_current_squad=False,
                            )
                        append_log(f"Recherche joueurs Wikipedia (en): {len(player_names)} noms extraits.", step=True)
                        if player_names:
                            roster_from_wikipedia = True
        except Exception as e:
            append_log(f"Recherche joueurs Wikipedia échouée: {e}", step=True)
            player_names = []
            roster_from_wikipedia = False

        if player_names:
            append_roster_preview_log("Effectif brut (avant enrichissement Instagram / Wikidata)", player_names)

        # Secondary: DuckDuckGo via Selenium (last resort) when Wikipedia yields nothing.
        driver = None
        by_cls = None
        webdrv_exc = Exception
        if not player_names:
            webdrv, webdrv_exc, by_cls = ensure_selenium()
            if webdrv is None or by_cls is None:
                append_log("Recherche joueurs: Selenium indisponible et liste joueurs vide.")
                return []

            # Prioritize roster-like queries for better quality player names.
            query_candidates = [
                f"effectif {club} {ville} {sport} {filters.saison}".strip(),
                f"liste joueurs {club} {ville} {sport}".strip(),
                f"{club} roster {sport} players".strip(),
                f"effectif {area} {sport}".strip(),
            ]
            try:
                options = webdrv.ChromeOptions()
                options.add_argument("--headless=new")
                options.add_argument("--disable-gpu")
                options.add_argument("--window-size=1600,1000")
                driver = webdrv.Chrome(options=options)
            except (ModuleNotFoundError, ImportError, webdrv_exc):
                append_log("Recherche joueurs: impossible de démarrer Chrome WebDriver.")
                return []

        rows: List[Dict[str, str]] = []
        analyzed = 0
        ig_stats = {"handles": 0, "bio": 0, "followers": 0, "posts": 0}
        try:
            self.after(
                0,
                lambda: (
                    self._search_dialog.set_indeterminate() if self._search_dialog is not None else None
                ),
            )
            self.after(
                0,
                lambda: self._update_search_dialog(
                    0, "Analyse de la liste joueurs…", analyzed=0, retained=0
                ),
            )
            if driver is not None and by_cls is not None and not player_names:
                for q in query_candidates:
                    self._raise_if_cancelled()
                    append_log(f"Fallback web joueurs: requête = {q}")
                    chunk = self._discover_player_names(driver, by_cls, q, filters.max_profiles * 3)
                    append_log(f"Fallback web joueurs: {len(chunk)} noms détectés sur cette requête")
                    for n in chunk:
                        if n not in player_names:
                            player_names.append(n)
                    if len(player_names) >= filters.max_profiles * 2:
                        break
            if not player_names:
                append_log("Recherche joueurs: aucun nom détecté (0).")
                return []

            # Progress should reflect the actual number of profiles we will try to retain.
            # The loop stops once we have `max_profiles` retained rows, so using `*2` makes the bar
            # stop at 50/66% for small runs (1-2 profiles).
            total_candidates = min(len(player_names), max(1, int(filters.max_profiles)))
            self.after(
                0,
                lambda t=total_candidates: (
                    self._search_dialog.set_determinate(t) if self._search_dialog is not None else None
                ),
            )

            for idx, name in enumerate(player_names, start=1):
                self._raise_if_cancelled()
                if len(rows) >= filters.max_profiles:
                    break

                append_log(
                    f"--- Enrichissement profil {idx}/{total_candidates}: {name} ---",
                    step=True,
                )
                analyzed = idx
                self.after(
                    0,
                    lambda i=idx, n=name, a=analyzed, r=len(rows): self._update_search_dialog(
                        i, f"Enrichissement: {n}…", analyzed=a, retained=r
                    ),
                )
                insta_url = None
                username = None
                # Optional: if Selenium is available, try find instagram page for better link (best-effort).
                if driver is not None and by_cls is not None:
                    try:
                        insta_url = self._find_instagram_profile_for_name(driver, by_cls, name, sport, area)
                        if not insta_url:
                            append_log(f"Recherche IG web: aucun profil trouvé pour '{name}'")
                        username = insta_url.rstrip("/").split("/")[-1] if insta_url else None
                    except SearchCancelled:
                        raise
                    except Exception as e:
                        append_log(f"Recherche IG web échouée pour '{name}': {e}")
                row = {c: "" for c in CSV_COLUMNS}
                nom, prenom = self._split_name(name)
                row["Nom"] = nom or username
                row["Prénom"] = prenom
                row["Date d'ajout"] = now_str()
                row["Sport"] = sport
                row["Club"] = club or area
                row["Ville"] = ville
                row["Instagram"] = insta_url or ""
                row["Nombre de points"] = ""
                row["Niveau Barème"] = ""
                row["Autres informations"] = ""
                row["Priorité"] = ""

                # Step: Wikipedia -> Wikidata (always continue)
                resolved_qid = wikipedia_resolve_wikidata_qid(name, club_hint=club)
                player_struct = self._build_player_struct(
                    name=name,
                    ig_username=username,
                    wikidata_qid=resolved_qid,
                    club_hint=club,
                    sport_hint=sport,
                    ville_hint=ville,
                )
                # Apply structured fields back to CSV row
                if player_struct.get("birth_date"):
                    row["Date de naissance"] = player_struct["birth_date"]
                    row["Age"] = calc_age_human(player_struct["birth_date"]) or str(player_struct.get("age") or "")
                if player_struct.get("nationality"):
                    row["Nationalité"] = player_struct["nationality"]
                if player_struct.get("bio"):
                    row["Info en bio"] = player_struct["bio"]
                if player_struct.get("instagram"):
                    row["Instagram"] = player_struct["instagram"]
                if player_struct.get("posts") is not None and not row.get("Nombre de posts"):
                    row["Nombre de posts"] = str(player_struct["posts"])
                if player_struct.get("posts_last_90d") is not None:
                    row["Posts 3 derniers mois"] = str(player_struct["posts_last_90d"])
                if player_struct.get("followers") is not None and not row.get("Nombre d'abonnés"):
                    row["Nombre d'abonnés"] = str(player_struct["followers"])
                if player_struct.get("instagram"):
                    ig_stats["handles"] += 1
                if player_struct.get("bio"):
                    ig_stats["bio"] += 1
                if player_struct.get("followers") is not None:
                    ig_stats["followers"] += 1
                if player_struct.get("posts") is not None:
                    ig_stats["posts"] += 1

                # Instagram enrichment removed (web-only). Keep nullable.

                rows.append(row)
                self.after(0, lambda rr=dict(row): self._add_rows([rr]))
                self.after(
                    0,
                    lambda i=idx, a=analyzed, r=len(rows), n=name: self._update_search_dialog(
                        i, f"Profil retenu: {n}", analyzed=a, retained=r
                    ),
                )
            # Ensure progress reaches 100% (especially when we stop early due to max_profiles).
            try:
                self.after(
                    0,
                    lambda t=total_candidates, a=analyzed, r=len(rows): self._update_search_dialog(
                        t, "Enrichissement terminé.", analyzed=a, retained=r
                    ),
                )
            except Exception:
                pass
            append_log(
                "Résumé IG: "
                f"handles={ig_stats['handles']}/{len(rows)} "
                f"bio={ig_stats['bio']} followers={ig_stats['followers']} posts={ig_stats['posts']}",
                step=True,
            )
            return rows
        finally:
            try:
                if driver is not None:
                    driver.quit()
            except Exception:
                pass

    def _build_player_struct(
        self,
        name: str,
        ig_username: Optional[str],
        wikidata_qid: Optional[str] = None,
        club_hint: str = "",
        sport_hint: str = "",
        ville_hint: str = "",
    ) -> dict:
        """Build final player structure with Wikipedia -> Wikidata fallback. Never raises."""
        out = {
            "name": name,
            "birth_date": None,
            "age": None,
            "nationality": None,
            "instagram": normalize_instagram_handle(ig_username) if ig_username else None,
            "followers": None,
            "posts": None,
            "posts_last_90d": None,
            "bio": None,
        }
        self._pending_serp_ig_stats = None
        handle_source = "input" if out.get("instagram") else "none"

        # Wikidata: prefer direct item (resolved from Wikipedia) to avoid homonyms.
        try:
            qid = (wikidata_qid or "").strip() or None
            if qid:
                append_log(f"Wikidata {name}: item direct {qid}")
            if not qid:
                qid = wikipedia_resolve_wikidata_qid(name, club_hint=club_hint)
                if qid:
                    append_log(f"Wikidata {name}: item via Wikipedia {qid}")
            if not qid:
                qid = wikidata_search_entity(name)
                if qid:
                    append_log(f"Wikidata {name}: fallback recherche texte {qid}")
            if qid:
                wd = wikidata_get_entity(qid)
                ent = wd.get("entities", {}).get(qid, {})
                if not wikidata_entity_is_human(ent):
                    append_log(f"Wikidata {name}: item {qid} ignoré (pas une fiche personne)")
                else:
                    bd = wikidata_claim_string(ent, "P569")
                    nat = None
                    # Nationality is entity reference (P27). Keep it simple: try label if present.
                    try:
                        p27 = ent.get("claims", {}).get("P27")
                        if p27:
                            nid = p27[0]["mainsnak"]["datavalue"]["value"]["id"]
                            nent = wikidata_get_entity(nid).get("entities", {}).get(nid, {})
                            nat = (nent.get("labels", {}).get("fr", {}) or {}).get("value") or (
                                nent.get("labels", {}).get("en", {}) or {}
                            ).get("value")
                    except Exception:
                        nat = None
                    ig = wikidata_claim_string(ent, "P2002")
                    if bd:
                        out["birth_date"] = bd
                        try:
                            out["age"] = int(calc_age(bd) or 0) or None
                        except Exception:
                            out["age"] = None
                    if nat:
                        out["nationality"] = nat
                    if ig:
                        out["instagram"] = normalize_instagram_handle(ig)
        except SearchCancelled:
            raise
        except Exception as e:
            append_log(f"Wikidata échoué pour {name}: {e}")

        if not out.get("birth_date"):
            try:
                bd_web = serp_guess_birth_date(
                    name,
                    club_hint=club_hint,
                    sport_hint=sport_hint,
                    ville_hint=ville_hint,
                )
                if bd_web:
                    out["birth_date"] = bd_web
                    try:
                        out["age"] = int(calc_age(bd_web) or 0) or None
                    except Exception:
                        out["age"] = None
            except Exception:
                pass

        # If no Instagram handle found via Wikidata, try local sources then (optionally) web discovery.
        if not out.get("instagram"):
            try:
                guessed = out.get("instagram") if out.get("instagram") else None
                guessed_preexisting = bool(guessed)

                if (not guessed) and (not self._ig_fast_mode_current):
                    append_log(f"[IG] {name}: handle absent, tentative résolution Google")
                    guessed = self._resolve_instagram_handle_google(
                        name, club_hint=club_hint or "", sport_hint=sport_hint or ""
                    )
                if not guessed:
                    if self._ig_fast_mode_current:
                        append_log(f"[IG] {name}: handle absent, DDG HTTP (premier lien, mode rapide)")
                        guessed = self._resolve_instagram_handle_http(
                            name,
                            fast_first=True,
                            sport_hint=sport_hint or "",
                            club_hint=club_hint or "",
                        )
                        if not guessed:
                            append_log(f"[IG] {name}: DDG vide, tentative Google HTTP (premier lien)")
                            guessed = self._resolve_instagram_handle_google_http_first(name, club_hint or "")
                    else:
                        append_log(f"[IG] {name}: handle absent, tentative résolution DDG HTTP")
                        guessed = self._resolve_instagram_handle_http(
                            name,
                            fast_first=False,
                            sport_hint=sport_hint or "",
                            club_hint=club_hint or "",
                        )
                # Keep selenium DDG as last resort only (mode complet).
                if (not guessed) and (not self._ig_fast_mode_current):
                    append_log(f"[IG] {name}: DDG/Google vides, tentative résolution selenium DDG")
                    guessed = self._resolve_instagram_handle_selenium(name)
                if guessed:
                    guessed_norm = normalize_instagram_handle(guessed)
                    if not guessed_norm or guessed_norm.lstrip("@").startswith("popular"):
                        append_log(f"[IG] {name}: handle rejeté (invalide) {guessed}")
                        guessed_norm = None
                    if guessed_norm:
                        out["instagram"] = guessed_norm
                        # Preserve original source (manual/wikidata/input) when handle already existed.
                        if not guessed_preexisting:
                            handle_source = "discovered"
                        append_log(f"[IG] {name}: handle trouvé {guessed_norm}")
                    else:
                        append_log(f"[IG] {name}: handle introuvable")
                else:
                    append_log(f"[IG] {name}: handle introuvable")
            except Exception as e:
                append_log(f"[IG] {name}: erreur résolution handle: {e}")

        pending_serp = self._pending_serp_ig_stats
        self._pending_serp_ig_stats = None
        if pending_serp and self._ig_fast_mode_current:
            if pending_serp.get("followers") is not None:
                out["followers"] = pending_serp["followers"]
            if pending_serp.get("posts") is not None:
                out["posts"] = pending_serp["posts"]
            append_log(
                f"[IG] {name}: abonnés/posts depuis extrait SERP (sans ouvrir instagram.com) "
                f"followers={out.get('followers')} posts={out.get('posts')}"
            )

        # Instagram public fallback: scrape bio/followers when we have a handle (skipped in fast mode).
        try:
            ig = out.get("instagram")
            ig_url = None
            if ig and isinstance(ig, str) and ig.startswith("@"):
                ig_url = f"https://www.instagram.com/{ig[1:]}"
            elif isinstance(ig, str) and "instagram.com" in ig:
                ig_url = ig
            if not ig_url:
                append_log(f"[IG] {name}: scraping ignoré (pas d'URL instagram)")
            elif self._ig_fast_mode_current:
                append_log(f"[IG] {name}: mode rapide, scraping public désactivé (handle conservé)")
                # Avec identifiants Instagram : complément léger (bio, posts 90j) via API auth uniquement.
                # En mode Rapide, le nombre d'abonnés/posts doit venir EXCLUSIVEMENT des extraits SERP
                # (DDG/Google) pour rester cohérent avec le mode choisi.
                if (
                    self._ig_scraper is not None
                    and getattr(self._ig_scraper, "has_auth_credentials", lambda: False)()
                    and ig_url
                ):
                    try:
                        uname = ig_url.rstrip("/").split("/")[-1]
                        append_log(f"[IG] {name}: complément auth (bio / posts 90j) @{uname}")
                        ig_data = self._ig_scraper.get_profile(uname, force_refresh=True)
                        if ig_data.get("bio"):
                            out["bio"] = ig_data["bio"]
                        if ig_data.get("posts_last_90d") is not None:
                            out["posts_last_90d"] = ig_data["posts_last_90d"]
                        append_log(
                            f"[IG] {name}: auth complété bio={'oui' if out.get('bio') else 'non'} "
                            f"posts_90j={out.get('posts_last_90d')}"
                        )
                    except Exception as ex:
                        append_log(f"[IG] {name}: complément auth échec: {ex}")
                if out.get("followers") is None and out.get("posts") is None:
                    append_log(
                        f"[IG] {name}: posts_90j=None: mode Rapide — aucun appel get_profile "
                        f"(posts 3 mois indisponible sans scrape; « Complet » pour tenter)"
                    )
                else:
                    if out.get("posts_last_90d") is None:
                        append_log(
                            f"[IG] {name}: posts_90j=None: mode Rapide — abonnés/posts viennent de l’extrait SERP "
                            f"(pas de posts sur 90 jours sans API/HTML profil)"
                        )
            else:
                append_log(f"[IG] {name}: scraping {ig_url}")
                if self._ig_scraper is not None:
                    # Force refresh for trusted/manual or newly discovered handle to avoid stale empty cache.
                    force_ig = handle_source in ("manual", "discovered")
                    ig_data = self._ig_scraper.get_profile(
                        ig_url.rstrip("/").split("/")[-1],
                        force_refresh=force_ig,
                    )
                    if ig_data.get("bio"):
                        out["bio"] = ig_data["bio"]
                    if ig_data.get("posts") is not None:
                        out["posts"] = ig_data["posts"]
                    if ig_data.get("followers") is not None:
                        out["followers"] = ig_data["followers"]
                    if ig_data.get("posts_last_90d") is not None:
                        out["posts_last_90d"] = ig_data["posts_last_90d"]
                else:
                    ig_data = instagram_public_scrape(ig_url)
                    if ig_data.get("bio"):
                        out["bio"] = ig_data["bio"]
                    if ig_data.get("posts") is not None:
                        out["posts"] = ig_data["posts"]
                    if ig_data.get("followers") is not None:
                        out["followers"] = ig_data["followers"]
                if ig_url and (out.get("followers") is None or out.get("posts") is None):
                    # 1) Essayer autour du handle courant (quand il est fiable).
                    ig_for_stats = out.get("instagram")
                    gs, gp = self._serp_google_stats_for_handle(name, club_hint or "", ig_for_stats)
                    if gs is not None and out.get("followers") is None:
                        out["followers"] = gs
                    if gp is not None and out.get("posts") is None:
                        out["posts"] = gp
                    if gs is not None or gp is not None:
                        append_log(
                            f"[IG] {name}: Google SERP — complément followers/posts "
                            f"(posts_90j impossible sans dates dans l’extrait ni API profil)"
                        )
                    # 2) Si rien trouvé et handle issu de Wikidata, refaire une passe
                    # purement sur « nom + instagram » (sans se baser sur ce handle),
                    # comme le ferait une recherche manuelle.
                    if handle_source == "wikidata" and (
                        out.get("followers") is None or out.get("posts") is None
                    ):
                        gs2, gp2 = self._serp_google_stats_for_handle(name, club_hint or "", None)
                        if gs2 is not None and out.get("followers") is None:
                            out["followers"] = gs2
                        if gp2 is not None and out.get("posts") is None:
                            out["posts"] = gp2
                        if gs2 is not None or gp2 is not None:
                            append_log(
                                f"[IG] {name}: Google SERP (nom+instagram) — complément followers/posts "
                                f"(fallback handle Wikidata)"
                            )
                if (
                    pending_serp
                    and not self._ig_fast_mode_current
                    and (out.get("followers") is None or out.get("posts") is None)
                ):
                    serp_filled = False
                    if out.get("followers") is None and pending_serp.get("followers") is not None:
                        out["followers"] = pending_serp["followers"]
                        serp_filled = True
                    if out.get("posts") is None and pending_serp.get("posts") is not None:
                        out["posts"] = pending_serp["posts"]
                        serp_filled = True
                    if serp_filled:
                        append_log(
                            f"[IG] {name}: DDGS SERP — complément followers/posts après scrape "
                            f"followers={out.get('followers')} posts={out.get('posts')}"
                        )
                append_log(
                    f"[IG] {name}: résultat bio={'oui' if out.get('bio') else 'non'} "
                    f"followers={out.get('followers')} posts={out.get('posts')} "
                    f"posts_90j={out.get('posts_last_90d')}"
                )
        except Exception:
            append_log(f"[IG] {name}: erreur scraping")
            pass

        return out

    def _discover_player_names(self, driver, by_cls, query: str, limit: int) -> List[str]:
        url = f"https://duckduckgo.com/?q={quote_plus(query)}"
        driver.get(url)
        self._sleep_with_cancel(2)
        anchors = driver.find_elements(by_cls.CSS_SELECTOR, "h2 a, a[data-testid='result-title-a']")
        names: List[str] = []
        seen = set()
        for a in anchors:
            self._raise_if_cancelled()
            text = (a.text or "").strip()
            if not text:
                continue
            candidates = re.findall(r"\b[A-Z][a-zA-ZÀ-ÿ'\-]+(?:\s+[A-Z][a-zA-ZÀ-ÿ'\-]+){1,2}\b", text)
            for c in candidates:
                key = c.lower().strip()
                if key in seen:
                    continue
                seen.add(key)
                names.append(c.strip())
                if len(names) >= limit:
                    return names
        return names

    def _find_instagram_profile_for_name(self, driver, by_cls, name: str, sport: str, area: str) -> Optional[str]:
        q = f"site:instagram.com {name} {sport} {area} officiel"
        driver.get(f"https://duckduckgo.com/?q={quote_plus(q)}")
        self._sleep_with_cancel(1.5)
        links = driver.find_elements(by_cls.CSS_SELECTOR, "a[href*='instagram.com/']")
        for link in links:
            href = (link.get_attribute("href") or "").strip()
            if not href:
                continue
            href = href.split("?")[0].rstrip("/")
            if "/p/" in href or "/reel/" in href:
                continue
            tail = href.split("/")[-1]
            if not tail or tail in ("instagram.com", "accounts", "explore"):
                continue
            return href
        return None

    # instaloader enrichment removed.

    def _close_search_dialog(self) -> None:
        if self._search_dialog is not None:
            self._search_dialog.close()
            self._search_dialog = None
        self._search_progress_current = 0
        self._search_progress_analyzed = 0
        self._search_progress_retained = 0
        self._search_cancel_event = None
        self._search_cancel_requested = False

    def _update_search_dialog(self, current: int, message: str, analyzed: int = 0, retained: int = 0) -> None:
        self._search_progress_current = int(current)
        self._search_progress_analyzed = int(analyzed)
        self._search_progress_retained = int(retained)
        if self._search_dialog is not None:
            # Throttle UI refreshes to avoid a too-fast / flickery progress dialog.
            # We still keep internal counters exact, but only repaint periodically.
            now_ts = time.time()
            last_ts = float(getattr(self, "_search_dialog_last_update_ts", 0.0) or 0.0)
            min_interval = float(getattr(self, "_search_dialog_min_update_interval_s", 0.25) or 0.25)
            total = int(getattr(self._search_dialog, "total", 0) or 0)
            force = current <= 0 or (total > 0 and current >= total)
            if force or (now_ts - last_ts) >= min_interval:
                setattr(self, "_search_dialog_last_update_ts", now_ts)
                self._search_dialog.update(current, message, analyzed=analyzed, retained=retained)

    # Direct Instagram search removed (instaloader not used). Web/Wikidata only.

    # Instagram session prompts removed (web-only).

    def _search_web_fallback(self, filters: SearchFilters) -> List[Dict[str, str]]:
        webdrv, webdrv_exc, by_cls = ensure_selenium()
        if webdrv is None or by_cls is None:
            append_log("Fallback Selenium indisponible: tentative fallback HTTP.")
            self.after(
                0,
                lambda: self._update_search_dialog(
                    0, "Fallback web HTTP en cours (sans navigateur)…", analyzed=0, retained=0
                ),
            )
            return self._search_web_fallback_http(filters)

        query = filters.query.replace(",", " ").strip()
        terms = [x for x in query.split() if x]
        if not terms:
            return []

        try:
            options = webdrv.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1600,1000")
            driver = webdrv.Chrome(options=options)
        except (ModuleNotFoundError, ImportError, webdrv_exc) as e:
            append_log(f"Fallback web Selenium indisponible: {e}")
            self.after(
                0,
                lambda: self._update_search_dialog(
                    0, "Selenium indisponible, bascule en fallback HTTP…", analyzed=0, retained=0
                ),
            )
            return self._search_web_fallback_http(filters)
        results: List[Dict[str, str]] = []
        seen = set()

        try:
            self.after(
                0,
                lambda: (
                    self._search_dialog.set_indeterminate() if self._search_dialog is not None else None
                ),
            )
            self.after(
                0,
                lambda: self._update_search_dialog(
                    0, "Fallback web Instagram en cours…", analyzed=0, retained=0
                ),
            )
            tail = (filters.sport or "").strip() or "athlete"
            search_query = f"site:instagram.com {query} {tail}".strip()
            url = f"https://duckduckgo.com/?q={quote_plus(search_query)}"
            driver.get(url)
            time.sleep(2)

            links = driver.find_elements(by_cls.CSS_SELECTOR, "a[href*='instagram.com/']")
            for link in links:
                if len(results) >= filters.max_profiles:
                    break
                href = (link.get_attribute("href") or "").strip()
                if not href or "/p/" in href:
                    continue
                base = href.split("?")[0].rstrip("/")
                username = base.split("/")[-1]
                if not username or username in seen:
                    continue
                seen.add(username)

                row = {c: "" for c in CSV_COLUMNS}
                row["Nom"] = username
                row["Prénom"] = ""
                row["Date d'ajout"] = now_str()
                row["Sport"] = terms[0] if terms else ""
                row["Club"] = terms[1] if len(terms) > 1 else ""
                row["Instagram"] = f"https://instagram.com/{username}"
                row["Nombre d'abonnés"] = ""
                row["Nombre de posts"] = ""
                row["Posts 3 derniers mois"] = ""
                row["Info en bio"] = ""
                row["Autres informations"] = ""
                row["Nombre de points"] = ""
                row["Niveau Barème"] = ""
                row["Nationalité"] = ""
                row["Priorité"] = ""
                results.append(row)
                current = len(results)
                self.after(
                    0,
                    lambda c=current, u=username: self._update_search_dialog(
                        c, f"Fallback web: profil @{u} détecté", analyzed=c, retained=c
                    ),
                )

            return results
        except Exception as e:
            append_log(f"Fallback web échoué: {e}")
            self.after(
                0,
                lambda: self._update_search_dialog(
                    0, "Fallback Selenium échoué, tentative HTTP…", analyzed=0, retained=0
                ),
            )
            return self._search_web_fallback_http(filters)
        finally:
            driver.quit()

    def _search_web_fallback_http(self, filters: SearchFilters) -> List[Dict[str, str]]:
        query = filters.query.replace(",", " ").strip()
        if not query:
            return []
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
        }
        tail = (filters.sport or "").strip() or "athlete"
        search_query = f"site:instagram.com {query} {tail}".strip()
        url = f"https://duckduckgo.com/html/?q={quote_plus(search_query)}"

        try:
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            append_log(f"Fallback HTTP indisponible: {e}")
            raise RuntimeError(
                "Recherche limitée par Instagram et fallback web indisponible. "
                "Vérifiez votre connexion Internet, proxy/VPN, puis réessayez."
            ) from e

        links = self._extract_instagram_links_from_html(text)
        if not links:
            return []

        results: List[Dict[str, str]] = []
        seen = set()
        for raw in links:
            if len(results) >= filters.max_profiles:
                break
            href = raw.split("?")[0].rstrip("/")
            if "/p/" in href or "/reel/" in href:
                continue
            username = href.split("/")[-1]
            if not username or username in seen:
                continue
            seen.add(username)

            row = {c: "" for c in CSV_COLUMNS}
            row["Nom"] = username
            row["Prénom"] = ""
            row["Date d'ajout"] = now_str()
            row["Sport"] = filters.sport
            row["Club"] = filters.club
            row["Ville"] = filters.ville
            row["Instagram"] = f"https://instagram.com/{username}"
            row["Nombre d'abonnés"] = ""
            row["Nombre de posts"] = ""
            row["Posts 3 derniers mois"] = ""
            row["Info en bio"] = ""
            row["Autres informations"] = ""
            row["Nombre de points"] = ""
            row["Niveau Barème"] = ""
            row["Nationalité"] = ""
            row["Priorité"] = ""
            results.append(row)
            c = len(results)
            self.after(
                0,
                lambda cc=c, u=username: self._update_search_dialog(
                    cc, f"Fallback HTTP: profil @{u} détecté", analyzed=cc, retained=cc
                ),
            )
        return results

    def _extract_instagram_links_from_html(self, html_text: str) -> List[str]:
        out: List[str] = []
        t = html_text or ""
        # DuckDuckGo HTML often embeds target URLs in "uddg=<urlencoded>".
        # Important: stop at '&' to avoid keeping '&rut=...' tracking tokens.
        for m in re.findall(r"uddg=([^&\"'\s>]+)", t, flags=re.IGNORECASE):
            try:
                decoded = requests.utils.unquote(m)
            except Exception:
                decoded = m
            decoded = html.unescape(decoded)
            if "instagram.com/" in decoded:
                out.append(decoded)
        # Alternate DDG encodings (lite / redirects).
        for m in re.findall(
            r"(?:https?:)?//(?:www\.)?duckduckgo\.com/l/\?[^\"'\s>]*uddg=([^&\"'\s>]+)",
            t,
            flags=re.IGNORECASE,
        ):
            try:
                decoded = requests.utils.unquote(m)
            except Exception:
                decoded = m
            decoded = html.unescape(decoded)
            if "instagram.com/" in decoded:
                out.append(decoded)
        # Direct hrefs to instagram (lite HTML, some SERP layouts).
        for m in re.findall(
            r'href=["\'](https?://(?:www\.)?instagram\.com/[A-Za-z0-9._/?#]+)',
            t,
            flags=re.IGNORECASE,
        ):
            out.append(html.unescape(m))
        # Escaped URLs inside JSON/scripts.
        for m in re.findall(r"https?:\\?/\\?/(?:www\.)?instagram\.com/[A-Za-z0-9._/]+", t):
            out.append(m.replace("\\", ""))
        # Fallback direct URL extraction (whole page).
        for m in re.findall(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._/]+", t):
            out.append(html.unescape(m))
        # De-duplicate while preserving order.
        seen = set()
        uniq = []
        for u in out:
            # Remove DDG tracking token that can leak into decoded URLs.
            u = u.split("&rut=", 1)[0]
            # Remove any query/fragment for clean profile URLs.
            u = u.split("?", 1)[0].split("#", 1)[0].rstrip("/")
            if "instagram.com/" not in u:
                continue
            if u in seen:
                continue
            seen.add(u)
            uniq.append(u)
        return uniq

    def _first_valid_instagram_handle_from_links(self, links: List[str]) -> Optional[str]:
        """Premier lien qui correspond à un profil /user/ (exclut /p/, /reel/, etc.)."""
        for link in links[:25]:
            handle = normalize_instagram_handle(link)
            if not handle:
                continue
            u = handle.lstrip("@").lower()
            if u in ("p", "reel", "explore", "accounts", "stories", "popular"):
                continue
            return handle
        return None

    def _extract_instagram_profile_from_search_href(self, href: str) -> Optional[str]:
        """
        Normalize Google/DDG result links to direct instagram profile URL.
        Handles redirect forms like /url?q=https://instagram.com/xxx
        """
        raw = (href or "").strip()
        if not raw:
            return None
        # Absolute Google redirect URL
        try:
            parsed = urlparse(raw)
            if parsed.netloc and "google." in parsed.netloc and parsed.path == "/url":
                qv = parse_qs(parsed.query).get("q", [])
                if qv:
                    raw = qv[0]
                else:
                    uv = parse_qs(parsed.query).get("url", [])
                    if uv:
                        raw = uv[0]
        except Exception:
            pass
        # Relative redirect URL
        if raw.startswith("/url?"):
            try:
                parsed_qs = parse_qs(urlparse(raw).query)
                q = parsed_qs.get("q", [])
                u = parsed_qs.get("url", [])
                if q:
                    raw = q[0]
                elif u:
                    raw = u[0]
            except Exception:
                pass
        raw = unquote(raw)
        raw = html.unescape(raw)
        if "instagram.com/" not in raw.lower():
            return None
        # Extract first profile-like segment robustly from noisy search hrefs.
        m = re.search(r"instagram\.com/([A-Za-z0-9._]+)/?", raw, re.IGNORECASE)
        if not m:
            return None
        tail = (m.group(1) or "").strip().lower()
        if not tail or tail in ("p", "reel", "explore", "accounts", "stories", "popular"):
            return None
        return f"https://www.instagram.com/{tail}/"

    def _extract_instagram_handles_from_text(self, text: str) -> List[str]:
        out: List[str] = []
        if not text:
            return out
        # Unescape common escaped URL forms from search engines (e.g. https:\/\/www.instagram.com\/user\/)
        text_unescaped = text.replace("\\/", "/")
        # 1) Direct profile URLs
        for m in re.findall(r"https?://(?:www\.)?instagram\.com/([A-Za-z0-9._]+)/?", text_unescaped):
            u = (m or "").strip().lower()
            if not u or u in ("p", "reel", "explore", "accounts", "stories"):
                continue
            out.append(f"@{u}")
        # 2) Visible handle pattern often present in result titles/snippets: "(@username)"
        for m in re.findall(r"\(@([A-Za-z0-9._]{2,30})\)", text_unescaped):
            u = (m or "").strip().lower()
            if not u or u in ("p", "reel", "explore", "accounts", "stories", "popular"):
                continue
            out.append(f"@{u}")
        # 3) Generic @username fallback (lower priority, still useful)
        for m in re.findall(r"@([A-Za-z0-9._]{2,30})", text_unescaped):
            u = (m or "").strip().lower()
            if not u or u in ("p", "reel", "explore", "accounts", "stories", "popular"):
                continue
            out.append(f"@{u}")
        # dedupe preserve order
        seen = set()
        uniq = []
        for h in out:
            if h in seen:
                continue
            seen.add(h)
            uniq.append(h)
        return uniq

    def _serp_num_looks_like_decimal(self, num_str: str) -> bool:
        """True si le segment ressemble à un décimal FR/EN (ex. 1,2 ou 935,9), pas à des milliers 1,234."""
        s = (num_str or "").strip().replace(" ", "").replace("\u202f", "")
        if not s:
            return False
        if s.count(",") == 1 and "." not in s:
            left, right = s.split(",")
            if right.isdigit() and 1 <= len(right) <= 2 and left.replace(".", "").isdigit():
                return True
        if s.count(".") == 1 and "," not in s:
            left, right = s.split(".")
            if left.isdigit() and right.isdigit():
                return True
        return False

    def _parse_km_int_serp(self, num_str: str, suffix: str) -> Optional[int]:
        """Interprète un nombre d’extrait SERP (12K, 1.2M, 1 234, 1,2 M, etc.)."""
        try:
            raw = (num_str or "").strip().replace(" ", "").replace("\u202f", "")
            if not raw:
                return None
            # Décimal FR court ex. 1,2 M (pas milliers 1,234)
            if "," in raw and "." not in raw:
                parts = raw.split(",")
                if len(parts) == 2 and len(parts[1]) <= 2 and len(parts[0]) <= 4:
                    raw = f"{parts[0]}.{parts[1]}"
                elif len(parts) == 2 and len(parts[1]) == 3 and parts[1].isdigit():
                    raw = raw.replace(",", "")
                else:
                    raw = raw.replace(",", ".")
            elif raw.count(",") == 1 and raw.count(".") == 0 and len(raw.split(",")[-1]) == 3:
                raw = raw.replace(",", "")
            elif "," in raw and "." in raw:
                raw = raw.replace(",", "")
            elif "," in raw:
                raw = raw.replace(",", ".")
            n = float(raw)
            s = (suffix or "").lower()
            if s == "k":
                return int(n * 1000)
            if s == "m":
                return int(n * 1_000_000)
            return int(n)
        except Exception:
            return None

    def _parse_instagram_serp_followers(self, t: str) -> Optional[int]:
        """
        Nombre d’abonnés depuis un extrait SERP. Priorité aux formulations FR (évite « 1M Followers »
        quand « Plus de 1,2 M abonnés » est présent) et aux « 935,9 k abonnés » sans « Plus de ».
        """
        def _parse_word_suffix_num(num_raw: str, word_suffix: str) -> Optional[int]:
            try:
                s = (num_raw or "").strip().replace("\u202f", "").replace(" ", "")
                # 123,8 -> 123.8 ; 123.456 -> 123456 (si entier de milliers)
                if "," in s and "." not in s:
                    if s.count(",") == 1 and len(s.split(",")[-1]) <= 2:
                        s = s.replace(",", ".")
                    else:
                        s = s.replace(",", "")
                if "." in s and s.count(".") == 1 and len(s.split(".")[-1]) == 3:
                    s = s.replace(".", "")
                n = float(s)
                w = (word_suffix or "").lower()
                if w.startswith("mil") and "milliard" not in w:
                    return int(n * 1_000_000)
                if w.startswith("milliard") or w.startswith("bill"):
                    return int(n * 1_000_000_000)
                if w.startswith("thousand"):
                    return int(n * 1_000)
                return int(n)
            except Exception:
                return None

        # A) « Plus de … M/k » + abonnés / followers (y compris « abonnes » sans accent API)
        p_plus = re.compile(
            r"(?is)plus\s+de\s+([\d][\d\s,\.]*)\s*([kKmM])\s*(?:abonnés|abonn[eè]s|abonnes|followers)\b",
        )
        fr_matches: List[Tuple[int, bool, int]] = []
        for m in p_plus.finditer(t):
            v = self._parse_km_int_serp(m.group(1).strip(), m.group(2).strip())
            if v is None:
                continue
            fr_matches.append((m.start(), self._serp_num_looks_like_decimal(m.group(1)), v))
        if fr_matches:
            dec = [x for x in fr_matches if x[1]]
            if dec:
                dec.sort(key=lambda x: x[0])
                return dec[0][2]
            fr_matches.sort(key=lambda x: x[0])
            return fr_matches[0][2]
        # B) « 935,9 k abonnés » (sans « Plus de »)
        p_abo = re.compile(
            r"(?is)([\d][\d\s,\.]*)\s*([kKmM])\s*(?:abonnés|abonn[eè]s|abonnes)\b",
        )
        abo_matches: List[Tuple[int, bool, int]] = []
        for m in p_abo.finditer(t):
            v = self._parse_km_int_serp(m.group(1).strip(), m.group(2).strip())
            if v is None:
                continue
            abo_matches.append((m.start(), self._serp_num_looks_like_decimal(m.group(1)), v))
        if abo_matches:
            dec = [x for x in abo_matches if x[1]]
            if dec:
                dec.sort(key=lambda x: x[0])
                return dec[0][2]
            abo_matches.sort(key=lambda x: x[0])
            return abo_matches[0][2]
        # B2) FR/EN en mots : "123,8 millions d'abonnés", "500 million followers"
        p_words = re.compile(
            r"(?is)([\d][\d\s,\.]*)\s*(millions?|milliards?|thousands?|billions?)\s*(?:d['’]\s*)?(?:abonnés|abonn[eè]s|abonnes|followers)\b",
        )
        w_matches: List[Tuple[int, int]] = []
        for m in p_words.finditer(t):
            v = _parse_word_suffix_num(m.group(1), m.group(2))
            if v is not None:
                w_matches.append((m.start(), v))
        if w_matches:
            w_matches.sort(key=lambda x: x[0])
            return w_matches[0][1]
        # B3) Entier brut: "121 345 678 followers" / "84 000 abonnés"
        p_plain = re.compile(r"(?is)([\d][\d\s\.,]{2,})\s*(?:abonnés|abonn[eè]s|abonnes|followers)\b")
        plain_matches: List[Tuple[int, int]] = []
        for m in p_plain.finditer(t):
            v = _parse_int_maybe(m.group(1))
            if v is not None and v >= 1000:
                plain_matches.append((m.start(), v))
        if plain_matches:
            plain_matches.sort(key=lambda x: x[0])
            return plain_matches[0][1]
        # C) Anglais : tous les « … Followers », préférer un nombre à décimal (1.2M) à un entier arrondi (1M)
        p_en = re.compile(r"(?is)([\d][\d\s,\.]*)\s*([kKmM])\s+Followers\b")
        en_cands: List[Tuple[bool, int, int]] = []
        for m in p_en.finditer(t):
            v = self._parse_km_int_serp(m.group(1).strip(), m.group(2).strip())
            if v is None:
                continue
            en_cands.append((self._serp_num_looks_like_decimal(m.group(1)), v, m.start()))
        if not en_cands:
            return None
        dec_c = [c for c in en_cands if c[0]]
        if dec_c:
            return max(dec_c, key=lambda c: c[1])[1]
        return max(en_cands, key=lambda c: c[1])[1]

    def _parse_instagram_serp_stats(self, text: str) -> Tuple[Optional[int], Optional[int]]:
        """
        Abonnés et nombre de posts depuis le titre / corps d’un résultat Google ou DDG
        (ex. « Plus de 346 k abonnés », « 345K followers · 626 following · 315 posts »).
        """
        if not text:
            return None, None
        t = text.replace("\u202f", " ").replace("\xa0", " ")
        followers: Optional[int] = self._parse_instagram_serp_followers(t)
        posts: Optional[int] = None
        for pat in (
            r"([\d][\d\s,\.]*)[\s]*([kKmM])[\s]*(?:posts|publications)\b",
            r"([\d][\d\s,\.]*)[\s]*([kKmM])?[\s]*(?:posts|publications)\b",
            r"·\s*([\d][\d\s,\.]*)[\s]*([kKmM])?[\s]*(?:posts|publications)\b",
            r"([\d][\d\s,\.]*)\s*(?:posts|publications)\b",
        ):
            pm = re.search(pat, t, re.IGNORECASE)
            if pm:
                posts = self._parse_km_int_serp(pm.group(1).strip(), (pm.group(2) or "").strip())
                if posts is not None:
                    break
        return followers, posts

    def _html_to_serp_plain(self, raw_html: str) -> str:
        """Allège le HTML Google pour que les regex trouvent « 345K followers · 315 posts »."""
        if not raw_html:
            return ""
        t = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw_html)
        t = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", t)
        t = re.sub(r"<br\s*/?>", " · ", t, flags=re.I)
        t = re.sub(r"</div>|</p>|</li>", " · ", t, flags=re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        t = html.unescape(t)
        return re.sub(r"\s+", " ", t).strip()

    def _serp_google_stats_for_handle(
        self, name: str, club_hint: str, ig: Optional[str]
    ) -> Tuple[Optional[int], Optional[int]]:
        """
        Requête Google « nom instagram » et parse abonnés/posts dans la page,
        comme une recherche manuelle « nom + instagram ».
        """
        q = f"{name} instagram {club_hint}".strip()
        url = f"https://www.google.com/search?hl=fr&num=15&q={quote_plus(q)}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.google.com/",
        }
        try:
            resp = requests.get(url, headers=headers, timeout=18)
            if resp.status_code >= 400:
                return None, None
            plain = self._html_to_serp_plain(resp.text or "")
            if plain:
                fw, pw = self._parse_instagram_serp_stats(plain)
                if fw is not None or pw is not None:
                    return fw, pw
        except Exception:
            pass

        # Fallback DDG HTML (quand Google est vide/limité), en gardant la même stratégie autour du handle.
        try:
            ddg_endpoints = (
                "https://html.duckduckgo.com/html/?q=",
                "https://duckduckgo.com/html/?q=",
                "https://lite.duckduckgo.com/lite/?q=",
            )
            q2 = f"{name} instagram {club_hint}".strip()
            dheaders = dict(headers)
            dheaders["Referer"] = "https://duckduckgo.com/"
            for ep in ddg_endpoints:
                _raise_if_cancelled_global()
                durl = f"{ep}{quote_plus(q2)}"
                dr = requests.get(durl, headers=dheaders, timeout=18)
                if dr.status_code >= 400:
                    continue
                dplain = self._html_to_serp_plain(dr.text or "")
                if not dplain:
                    continue
                fw, pw = self._parse_instagram_serp_stats(dplain)
                if fw is not None or pw is not None:
                    return fw, pw
        except Exception:
            pass
        return None, None

    def _ddgs_result_blob_and_links(self, r: dict) -> Tuple[str, List[str]]:
        """Titre puis corps (comme à l’écran) pour favoriser les extraits FR « Plus de … abonnés »."""
        blob = f"{(r.get('title') or '').strip()} {(r.get('body') or '').strip()}"
        links: List[str] = []
        href = (r.get("href") or r.get("url") or "").strip()
        if href and "instagram.com" in href.lower():
            clean = href.split("?")[0].split("#")[0].rstrip("/")
            if clean:
                links.append(clean)
        for m in re.findall(
            r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._/?#-]+", blob, flags=re.I
        ):
            c = m.split("?")[0].split("#")[0].rstrip("/")
            if c:
                links.append(c)
        return blob, links

    def _ddgs_attach_serp_stats_for_handle(
        self, name: str, handle: str, row_snapshots: List[Tuple[str, List[str]]]
    ) -> None:
        """Remplit _pending_serp_ig_stats depuis la ligne DDGS qui contient ce profil (mode complet)."""
        hn = normalize_instagram_handle(handle)
        if not hn or not row_snapshots:
            return
        u_target = hn.lstrip("@").lower()
        for blob, row_links in row_snapshots:
            for lk in row_links:
                lu = normalize_instagram_handle(lk)
                if not lu or lu.lstrip("@").lower() != u_target:
                    continue
                fol, pst = self._parse_instagram_serp_stats(blob)
                if fol is not None or pst is not None:
                    self._pending_serp_ig_stats = {"followers": fol, "posts": pst}
                    append_log(
                        f"[IG] {name}: DDGS stats SERP (ligne du handle {u_target}) "
                        f"followers={fol} posts={pst}"
                    )
                return

    def _instagram_handle_from_ddgs(
        self,
        name: str,
        fast_first: bool,
        sport_hint: str = "",
        club_hint: str = "",
    ) -> Optional[str]:
        """
        Utilise le paquet ddgs (ex duckduckgo-search) pour des résultats structurés.
        Une simple requête HTTP sur duckduckgo.com/html renvoie souvent du HTML vide pour les scripts.
        """
        try:
            from ddgs import DDGS
        except ImportError:
            append_log("[IG] Installez ddgs: pip install ddgs")
            return None
        # Sans guillemets autour du nom : les requêtes « "Prénom Nom" instagram » renvoient souvent 0 lien IG.
        dis = " ".join(x for x in [(sport_hint or "").strip(), (club_hint or "").strip()] if x).strip()
        queries = []
        if dis:
            queries.append(f"{name} {dis} instagram")
            queries.append(f"instagram {name} {dis}")
        queries.extend(
            [
                f"{name} instagram",
                f"instagram {name}",
            ]
        )

        def links_from_rows(rows: List[dict]) -> List[str]:
            out: List[str] = []
            for row in rows:
                _, row_links = self._ddgs_result_blob_and_links(row)
                out.extend(row_links)
            return out

        append_log(f"[IG] {name}: DDGS start", step=True)
        merged: List[str] = []
        row_snapshots_all: List[Tuple[str, List[str]]] = []
        for q in queries:
            try:
                _raise_if_cancelled_global()
                _sleep_interruptible(0.35)
                ddgs = DDGS()
                rows = list(ddgs.text(q, max_results=25))
                if fast_first:
                    for r in rows:
                        _raise_if_cancelled_global()
                        blob, single_links = self._ddgs_result_blob_and_links(r)
                        if not single_links:
                            continue
                        picked = self._first_valid_instagram_handle_from_links(single_links)
                        if picked:
                            fol, pst = self._parse_instagram_serp_stats(blob)
                            self._pending_serp_ig_stats = {"followers": fol, "posts": pst}
                            append_log(
                                f"[IG] {name}: DDGS {len(rows)} résultat(s), stats SERP "
                                f"followers={fol} posts={pst}"
                            )
                            append_log(f"[IG] {name}: DDGS — premier profil {picked}")
                            append_log(f"[IG] {name}: DDGS done ({picked})", step=True)
                            return picked
                    append_log(f"[IG] {name}: DDGS {len(rows)} résultat(s), aucun profil pour «{q[:44]}…»")
                    continue
                for r in rows:
                    _raise_if_cancelled_global()
                    blob, row_links = self._ddgs_result_blob_and_links(r)
                    if row_links:
                        row_snapshots_all.append((blob, row_links))
                batch = links_from_rows(rows)
                append_log(f"[IG] {name}: DDGS {len(rows)} résultat(s), {len(batch)} lien(s) IG pour «{q[:44]}…»")
                merged.extend(batch)
            except Exception as ex:
                append_log(f"[IG] {name}: DDGS erreur ({q[:32]}…): {ex}")
                continue

        if fast_first:
            append_log(f"[IG] {name}: DDGS done (none)", step=True)
            return None

        if not merged:
            append_log(f"[IG] {name}: DDGS done (0 liens)", step=True)
            return None
        seen = set()
        uniq: List[str] = []
        for u in merged:
            u = u.split("?")[0].split("#")[0].rstrip("/")
            if u in seen:
                continue
            seen.add(u)
            uniq.append(u)
        # With club/sport disambiguation in query, keep first valid profile link.
        if dis:
            # Premier lien IG trouvé pour les requêtes avec club/sport,
            # comme une recherche manuelle « nom club instagram ».
            picked = self._first_valid_instagram_handle_from_links(uniq)
            if picked:
                self._ddgs_attach_serp_stats_for_handle(name, picked, row_snapshots_all)
                append_log(f"[IG] {name}: DDGS premier lien (avec club/sport) {picked}")
                append_log(f"[IG] {name}: DDGS done ({picked})", step=True)
                return picked
        name_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", name or "") if len(t) >= 3]
        best = None
        best_score = -1.0
        for link in uniq[:24]:
            handle = normalize_instagram_handle(link)
            if not handle:
                continue
            u = handle.lstrip("@").lower()
            if u in ("p", "reel", "explore", "accounts"):
                continue
            score = 0.0
            for t in name_tokens:
                if t in u:
                    score += 2.0
            score -= max(0, len(u) - 20) * 0.05
            if score > best_score:
                best_score = score
                best = handle
        if best_score > 0 and best:
            self._ddgs_attach_serp_stats_for_handle(name, best, row_snapshots_all)
            append_log(f"[IG] {name}: DDGS handle retenu (score) {best}")
            append_log(f"[IG] {name}: DDGS done ({best})", step=True)
            return best
        picked = self._first_valid_instagram_handle_from_links(uniq)
        if picked:
            self._ddgs_attach_serp_stats_for_handle(name, picked, row_snapshots_all)
            append_log(f"[IG] {name}: DDGS fallback premier lien {picked}")
            append_log(f"[IG] {name}: DDGS done ({picked})", step=True)
            return picked
        append_log(f"[IG] {name}: DDGS done (none)", step=True)
        return None

    def _resolve_instagram_handle_http(
        self,
        name: str,
        fast_first: bool = False,
        sport_hint: str = "",
        club_hint: str = "",
    ) -> Optional[str]:
        """
        Lightweight DDG lookup to find an Instagram profile URL for a player name.
        Returns normalized handle like '@username' or None.
        If fast_first is True (mode rapide), take the first valid profile link (like a web search),
        instead of scoring by name tokens in the username (often absent e.g. motya_39).
        """
        self._pending_serp_ig_stats = None
        append_log(f"[IG] {name}: résolution handle (DDG) start", step=True)
        via = self._instagram_handle_from_ddgs(
            name,
            fast_first=fast_first,
            sport_hint=sport_hint,
            club_hint=club_hint,
        )
        if via:
            append_log(f"[IG] {name}: résolution handle (DDG) done ({via})", step=True)
            return via

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Referer": "https://duckduckgo.com/",
        }
        dis = " ".join(x for x in [sport_hint, club_hint] if (x or "").strip()).strip()
        # Several queries + HTML endpoints: DDG often returns empty/minimal HTML on one combination.
        queries = [
            f"{name} instagram",
            f"instagram {name}",
        ]
        if dis:
            queries.insert(0, f"site:instagram.com {name} {dis}")
        else:
            queries.append(f"site:instagram.com {name}")
        endpoints = [
            "https://html.duckduckgo.com/html/?q=",
            "https://duckduckgo.com/html/?q=",
            "https://lite.duckduckgo.com/lite/?q=",
        ]
        try:
            append_log(f"[IG] {name}: DDG lookup handle")
            links: List[str] = []
            last_status = None
            for q in queries:
                for ep in endpoints:
                    _raise_if_cancelled_global()
                    url = f"{ep}{quote_plus(q)}"
                    try:
                        resp = requests.get(url, headers=headers, timeout=18)
                        last_status = resp.status_code
                        if resp.status_code >= 400:
                            continue
                        body = resp.text or ""
                        batch = self._extract_instagram_links_from_html(body)
                        if not batch:
                            continue
                        host = ep.split("/")[2]
                        append_log(f"[IG] {name}: DDG liens extraits ({len(batch)}) via {host}")
                        if fast_first:
                            picked = self._first_valid_instagram_handle_from_links(batch)
                            if picked:
                                fol, pst = self._parse_instagram_serp_stats(body)
                                self._pending_serp_ig_stats = {"followers": fol, "posts": pst}
                                append_log(
                                    f"[IG] {name}: DDG HTML stats SERP followers={fol} posts={pst}"
                                )
                                append_log(f"[IG] {name}: DDG premier lien retenu {picked}")
                                append_log(f"[IG] {name}: résolution handle (DDG) done ({picked})", step=True)
                                return picked
                            append_log(f"[IG] {name}: DDG liens non profils, autre tentative…")
                            continue
                        links = batch
                        append_log(f"[IG] {name}: DDG liens trouvés ({len(links)}) via {host}")
                        break
                    except Exception:
                        continue
                if links:
                    break
            if fast_first:
                append_log(f"[IG] {name}: DDG aucun profil Instagram exploitable après tentatives")
                append_log(f"[IG] {name}: résolution handle (DDG) done (none)", step=True)
                return None
            if not links:
                append_log(
                    f"[IG] {name}: DDG aucun lien instagram"
                    + (f" (dernier HTTP {last_status})" if last_status is not None else "")
                )
                append_log(f"[IG] {name}: résolution handle (DDG) done (0 liens)", step=True)
                return None

            name_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", name or "") if len(t) >= 3]
            best = None
            best_score = -1
            for link in links[:12]:
                handle = normalize_instagram_handle(link)
                if not handle:
                    continue
                u = handle.lstrip("@").lower()
                # Filter obvious non-profile paths
                if u in ("p", "reel", "explore", "accounts"):
                    continue
                score = 0
                for t in name_tokens:
                    if t in u:
                        score += 2
                # Prefer shorter clean usernames when score ties.
                score -= max(0, len(u) - 20) * 0.05
                if score > best_score:
                    best = handle
                    best_score = score
            if best_score > 0:
                append_log(f"[IG] {name}: résolution handle (DDG) done ({best})", step=True)
                return best
            append_log(f"[IG] {name}: DDG liens non pertinents")
            append_log(f"[IG] {name}: résolution handle (DDG) done (none)", step=True)
            return None
        except Exception:
            append_log(f"[IG] {name}: DDG erreur")
            append_log(f"[IG] {name}: résolution handle (DDG) done (error)", step=True)
            return None

    def _resolve_instagram_handle_google_http_first(self, name: str, club_hint: str = "") -> Optional[str]:
        """
        Lightweight Google HTTP lookup (no Selenium): keep the first valid Instagram profile link.
        Useful when DDG returns empty SERP to script requests.
        """
        self._pending_serp_ig_stats = None
        q = f"{name} instagram {club_hint}".strip()
        url = f"https://www.google.com/search?hl=fr&num=10&q={quote_plus(q)}"
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Referer": "https://www.google.com/",
        }
        try:
            append_log(f"[IG] {name}: Google HTTP lookup handle")
            resp = requests.get(url, headers=headers, timeout=18)
            if resp.status_code >= 400:
                append_log(f"[IG] {name}: Google HTTP {resp.status_code}")
                return None
            body = resp.text or ""
            candidates: List[str] = []

            for m in re.findall(r'href="(/url\?q=[^"]+)"', body):
                profile = self._extract_instagram_profile_from_search_href(m)
                if profile:
                    candidates.append(profile)
            for m in re.findall(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._/?#-]+", body, flags=re.I):
                candidates.append(m)

            picked = self._first_valid_instagram_handle_from_links(candidates)
            if picked:
                fol, pst = self._parse_instagram_serp_stats(body)
                self._pending_serp_ig_stats = {"followers": fol, "posts": pst}
                append_log(
                    f"[IG] {name}: Google HTTP stats SERP followers={fol} posts={pst}"
                )
                append_log(f"[IG] {name}: Google HTTP premier lien retenu {picked}")
                return picked
            append_log(f"[IG] {name}: Google HTTP aucun profil instagram exploitable")
            return None
        except Exception:
            append_log(f"[IG] {name}: Google HTTP erreur")
            return None

    def _resolve_instagram_handle_selenium(self, name: str) -> Optional[str]:
        webdrv, webdrv_exc, by_cls = ensure_selenium()
        if webdrv is None or by_cls is None:
            append_log(f"[IG] {name}: selenium indisponible")
            return None

    def _resolve_instagram_handle_google(
        self, name: str, club_hint: str = "", sport_hint: str = ""
    ) -> Optional[str]:
        webdrv, webdrv_exc, by_cls = ensure_selenium()
        if webdrv is None or by_cls is None:
            append_log(f"[IG] {name}: google selenium indisponible")
            return None
        driver = None
        try:
            options = webdrv.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1400,900")
            options.add_argument("--disable-blink-features=AutomationControlled")
            driver = webdrv.Chrome(options=options)

            extra = " ".join(
                x for x in [(sport_hint or "").strip(), (club_hint or "").strip()] if x
            ).strip()
            q = (f"{name} instagram {extra}".strip() if extra else f"{name} instagram")
            driver.get(f"https://www.google.com/search?q={quote_plus(q)}")
            self._sleep_with_cancel(1.8)

            # Google results are often redirect links /url?q=...
            links = driver.find_elements(by_cls.CSS_SELECTOR, "a[href]")
            if not links:
                append_log(f"[IG] {name}: google aucun lien instagram")
                # continue with page_source extraction below

            candidates = []
            for a in links[:40]:
                href = (a.get_attribute("href") or "").strip()
                profile_url = self._extract_instagram_profile_from_search_href(href)
                if not profile_url:
                    continue
                handle = normalize_instagram_handle(profile_url)
                if not handle:
                    continue
                u = handle.lstrip("@").lower()
                if u in ("p", "reel", "explore", "accounts"):
                    continue
                candidates.append(handle)

            # Fallback: parse rendered HTML source directly for instagram URLs.
            if not candidates:
                try:
                    src = driver.page_source or ""
                    candidates.extend(self._extract_instagram_handles_from_text(src))
                except Exception:
                    pass

            if not candidates:
                append_log(f"[IG] {name}: google liens non exploitables")
                return None

            name_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", name or "") if len(t) >= 3]
            club_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", club_hint or "") if len(t) >= 2]
            sport_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", sport_hint or "") if len(t) >= 2]
            best = None
            best_score = -1.0
            for h in candidates:
                u = h.lstrip("@").lower()
                score = 0.0
                for t in name_tokens:
                    if t in u:
                        score += 2.5
                for t in club_tokens:
                    if t in u:
                        score += 0.5
                for t in sport_tokens:
                    if t in u:
                        score += 0.35
                score -= max(0, len(u) - 20) * 0.05
                if score > best_score:
                    best_score = score
                    best = h

            # User preference: in practice first valid link is often correct.
            # If score is weak but we have candidates, keep first one as pragmatic fallback.
            if best_score > 0 and best:
                append_log(f"[IG] {name}: google candidat {best} (score={best_score:.2f})")
                return best
            if candidates:
                append_log(f"[IG] {name}: google score insuffisant, fallback premier lien {candidates[0]}")
                return candidates[0]
            append_log(f"[IG] {name}: google score insuffisant")
            return None
        except (webdrv_exc, Exception):
            append_log(f"[IG] {name}: google selenium erreur lookup")
            return None
        finally:
            try:
                if driver is not None:
                    driver.quit()
            except Exception:
                pass
        driver = None
        try:
            options = webdrv.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1400,900")
            driver = webdrv.Chrome(options=options)
            dis = " ".join(x for x in [sport_hint, club_hint] if (x or "").strip()).strip()
            q = (
                f"site:instagram.com {name} {dis}".strip()
                if dis
                else f"site:instagram.com {name}"
            )
            driver.get(f"https://duckduckgo.com/?q={quote_plus(q)}")
            self._sleep_with_cancel(1.5)
            links = driver.find_elements(by_cls.CSS_SELECTOR, "a[href*='instagram.com/']")
            candidates = []
            for a in links[:20]:
                href = (a.get_attribute("href") or "").strip()
                handle = normalize_instagram_handle(href)
                if not handle:
                    continue
                user = handle.lstrip("@").lower()
                if user in ("p", "reel", "explore", "accounts"):
                    continue
                candidates.append(handle)
            if not candidates:
                append_log(f"[IG] {name}: selenium aucun lien instagram")
                return None
            # simple name-token scoring
            name_tokens = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ]+", name or "") if len(t) >= 3]
            best = None
            best_score = -1
            for h in candidates:
                u = h.lstrip("@").lower()
                score = sum(2 for t in name_tokens if t in u)
                score -= max(0, len(u) - 20) * 0.05
                if score > best_score:
                    best_score = score
                    best = h
            return best if best_score > 0 else None
        except (webdrv_exc, Exception):
            append_log(f"[IG] {name}: selenium erreur lookup")
            return None
        finally:
            try:
                if driver is not None:
                    driver.quit()
            except Exception:
                pass

    def on_connect_instagram(self) -> None:
        append_log("Bouton 'Connexion Instagram' cliqué.")
        # Re-run public init on demand.
        t = threading.Thread(target=self._init_instagram_public_non_blocking, daemon=True)
        t.start()
        messagebox.showinfo(
            f"{APP_TITLE} - Instagram",
            "Instagram: mode public (sans connexion).\n\n"
            "L'application tente automatiquement de récupérer (si possible) :\n"
            "- bio Instagram\n"
            "- nombre d'abonnés\n"
            "- nombre de posts\n\n"
            "Si Instagram bloque (rate-limit/403/429), la recherche continue sans erreur.",
        )

    def on_test_instagram(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            "Test Instagram direct désactivé.\n"
            "La recherche continue via le Web même si Instagram limite l'accès.",
        )

    def on_update_app(self) -> None:
        """
        Update button for the 'git + venv' delivery: closes the app, pulls latest code, updates deps,
        and relaunches app via batch script.
        """
        # Platform-specific updater:
        # - Windows: ZIP updater (.bat) works even when client has no Git installed.
        # - macOS: updater script replaces the packaged .app (latest GitHub Release asset).
        is_macos = sys.platform == "darwin"
        is_windows = os.name == "nt"
        if is_macos:
            updater_in_bundle = _macos_updater_script_path()
            if not updater_in_bundle:
                messagebox.showerror(
                    APP_TITLE,
                    "Script de mise à jour macOS introuvable dans l'application.\n"
                    "Réinstallez depuis la dernière release GitHub, ou reconstruisez le .app "
                    "avec PyInstaller (fichier MiseAJour_macOS_Et_Relance.sh embarqué).",
                )
                return
        else:
            bat = os.path.join(APP_DIR, "MiseAJour_Zip_Et_Relance.bat")
            if not os.path.exists(bat):
                messagebox.showerror(APP_TITLE, f"Script de mise à jour introuvable:\n{bat}")
                return
        if not messagebox.askyesno(
            APP_TITLE,
            "L'application va se fermer, télécharger les mises à jour, puis se relancer.\n\nContinuer ?",
        ):
            return
        try:
            # Persist currently opened CSV so the relaunched app restores it.
            self._save_pending_csv_for_update()
            # Spawn an updater UI that survives closing the app (if shipped next to app.py / in bundle).
            ui_py = os.path.join(APP_DIR, "update_ui.py")
            if not os.path.exists(ui_py) and _is_frozen():
                alt = _resource_path("update_ui.py")
                if os.path.isfile(alt):
                    ui_py = alt
            if os.path.exists(ui_py):
                subprocess.Popen(
                    [sys.executable, ui_py],
                    cwd=os.path.dirname(ui_py) or APP_DIR,
                    close_fds=True,
                    stdin=subprocess.DEVNULL,
                )
            if is_macos:
                app_bundle = self._find_macos_app_bundle_path()
                if not app_bundle:
                    messagebox.showerror(
                        APP_TITLE,
                        "Impossible de détecter le bundle .app.\n"
                        "La mise à jour macOS nécessite une application packagée (.app).",
                    )
                    return
                # Copy updater to a writable temp location (PyInstaller bundle can be read-only).
                tmp_dir = tempfile.mkdtemp(prefix="athletes_upd_")
                updater_tmp = os.path.join(tmp_dir, "MiseAJour_macOS_Et_Relance.sh")
                shutil.copy2(updater_in_bundle, updater_tmp)
                try:
                    os.chmod(updater_tmp, os.stat(updater_tmp).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                except Exception:
                    pass
                subprocess.Popen(
                    ["/bin/bash", updater_tmp, app_bundle],
                    cwd=tmp_dir,
                    close_fds=True,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
            elif is_windows:
                # Run updater without opening a cmd window for client.
                no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                subprocess.Popen(
                    ["cmd.exe", "/c", bat],
                    cwd=APP_DIR,
                    close_fds=True,
                    creationflags=no_window,
                )
            else:
                messagebox.showerror(APP_TITLE, f"OS non supporté pour la MAJ: {platform.platform()}")
                return
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Impossible de lancer la mise à jour:\n{e}")
            return

        # Close app so files are not locked during git pull / pip install.
        try:
            self.destroy()
        except Exception:
            sys.exit(0)

    def _find_macos_app_bundle_path(self) -> Optional[str]:
        """
        When packaged as a macOS .app (e.g. PyInstaller), sys.executable typically looks like:
        /.../Athletes Searcher.app/Contents/MacOS/Athletes Searcher
        Resolve the .app bundle path (any depth under Contents/MacOS/...).
        """
        try:
            exe = os.path.abspath(sys.executable or "")
            if not exe:
                return None
            parts = exe.split(os.sep)
            for i, seg in enumerate(parts):
                if seg.lower().endswith(".app"):
                    return os.sep.join(parts[: i + 1])
            p = exe
            for _ in range(14):
                if p.lower().endswith(".app") and os.path.isdir(p):
                    return p
                p2 = os.path.dirname(p)
                if p2 == p:
                    break
                p = p2
        except Exception:
            return None
        return None

    def _split_name(self, full_name: str) -> (str, str):
        if not full_name:
            return "", ""
        parts = full_name.split()
        if len(parts) == 1:
            return parts[0], ""
        return parts[-1], " ".join(parts[:-1])

    def _add_rows(self, rows: List[Dict[str, str]]) -> None:
        for row in rows:
            values = [row.get(col, "") for col in CSV_COLUMNS]
            self.tree.insert("", "end", values=values)

    def _tree_rows(self) -> List[Dict[str, str]]:
        out = []
        for iid in self.tree.get_children():
            values = self.tree.item(iid, "values")
            out.append({col: values[idx] if idx < len(values) else "" for idx, col in enumerate(CSV_COLUMNS)})
        return out

    def _write_csv(self, path: str) -> None:
        ensure_app_folders()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            # French-friendly CSV (Excel): semicolon separator.
            writer = csv.DictWriter(f, fieldnames=CSV_EXPORT_COLUMNS, delimiter=";")
            writer.writeheader()
            seen = set()
            for row in self._tree_rows():
                cooked = self._format_row_for_export(row)
                dedup_key = (
                    (cooked.get("Nom") or "").strip().lower(),
                    (cooked.get("Prénom") or "").strip().lower(),
                    (cooked.get("Date de naissance") or "").strip(),
                )
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                writer.writerow(cooked)

    def _format_date_fr(self, value: str) -> str:
        s = (value or "").strip()
        if not s:
            return ""
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return dt.datetime.strptime(s, fmt).strftime("%d/%m/%Y")
            except ValueError:
                continue
        return s

    def _format_age_human(self, age_value: str, birth_date: str) -> str:
        v = (age_value or "").strip()
        if birth_date:
            s = calc_age_human(birth_date)
            if s:
                return s
        if v:
            m = re.search(r"\d+", v)
            if m:
                return f"{int(m.group(0))} ans"
        return ""

    def _format_row_for_export(self, row: Dict[str, str]) -> Dict[str, str]:
        src = {c: (row.get(c, "") or "") for c in CSV_COLUMNS}
        out = {c: "" for c in CSV_EXPORT_COLUMNS}

        # 1:1 shared columns
        for k in (
            "Nom",
            "Prénom",
            "Date d'ajout",
            "Sport",
            "Date de naissance",
            "Age",
            "Club",
            "Ville",
            "Instagram",
            "Nombre d'abonnés",
            "Nombre de points",
            "Niveau Barème",
            "Info en bio",
            "Autres informations",
            "Nationalité",
            "Priorité",
        ):
            out[k] = src.get(k, "")

        # Header differences expected by client CSV.
        out["Nombre de post"] = src.get("Nombre de posts", "")
        out["Post 3 derniers mois"] = src.get("Posts 3 derniers mois", "")

        out["Date d'ajout"] = self._format_date_fr(out.get("Date d'ajout", ""))
        out["Date de naissance"] = self._format_date_fr(out.get("Date de naissance", ""))
        out["Age"] = self._format_age_human(src.get("Age", ""), out.get("Date de naissance", ""))

        insta = (out.get("Instagram", "") or "").strip()
        if insta.startswith("http"):
            handle = normalize_instagram_handle(insta)
            out["Instagram"] = handle[1:] if handle and handle.startswith("@") else (handle or insta)

        return out

    def _autosave_csv(self, query: str, *, timing_step: bool = False) -> None:
        if not self.current_csv_path:
            self.current_csv_path = generate_search_csv_path(query)
        self._write_csv(self.current_csv_path)
        self._set_status(f"Auto-sauvegarde: {self.current_csv_path}", step=timing_step)

    def on_export_csv(self) -> None:
        suggested_name = generate_export_filename(self._current_search_text())
        path = filedialog.asksaveasfilename(
            title="Exporter en CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=suggested_name,
            initialdir=ROOT_DIR,
        )
        if not path:
            return
        self._write_csv(path)
        self.current_csv_path = path
        self._set_status(f"Exporté: {path}")
        messagebox.showinfo(APP_TITLE, f"CSV exporté:\n{path}")

    def _write_excel_from_template(self, path: str) -> None:
        template = ensure_excel_template()
        if not template:
            raise RuntimeError("Template Excel indisponible (module openpyxl manquant).")
        shutil.copy2(template, path)
        try:
            from openpyxl import load_workbook
        except Exception as e:
            raise RuntimeError(f"openpyxl indisponible: {e}") from e

        wb = load_workbook(path)
        ws = wb.active
        if ws.max_row >= 2:
            ws.delete_rows(2, ws.max_row - 1)

        seen = set()
        for row in self._tree_rows():
            cooked = self._format_row_for_export(row)
            dedup_key = (
                (cooked.get("Nom") or "").strip().lower(),
                (cooked.get("Prénom") or "").strip().lower(),
                (cooked.get("Date de naissance") or "").strip(),
            )
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            ws.append([cooked.get(c, "") for c in CSV_EXPORT_COLUMNS])

        wb.save(path)

    def on_export_excel(self) -> None:
        suggested_name = generate_export_filename(self._current_search_text()).replace(".csv", ".xlsx")
        path = filedialog.asksaveasfilename(
            title="Exporter en Excel (template)",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile=suggested_name,
            initialdir=ROOT_DIR,
        )
        if not path:
            return
        try:
            self._write_excel_from_template(path)
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Impossible d'exporter en Excel:\n{e}")
            return
        self._set_status(f"Exporté (Excel): {path}")
        messagebox.showinfo(APP_TITLE, f"Excel exporté:\n{path}")

    def _build_final_results_from_table(self) -> None:
        """Build final JSON-friendly structure from current table. Never raises."""
        results = []
        for row in self._tree_rows():
            name = " ".join(x for x in [row.get("Prénom", ""), row.get("Nom", "")] if x).strip()
            birth = (row.get("Date de naissance") or "").strip() or None
            age_val = row.get("Age") or ""
            try:
                age_int = int(re.findall(r"\d+", str(age_val))[0]) if age_val else None
            except Exception:
                age_int = None
            insta = row.get("Instagram") or ""
            insta_handle = normalize_instagram_handle(insta) if insta else None
            posts = row.get("Nombre de posts") or ""
            try:
                posts_int = int(re.findall(r"\d+", str(posts))[0]) if posts else None
            except Exception:
                posts_int = None
            results.append(
                {
                    "name": name or None,
                    "birth_date": birth,
                    "age": age_int,
                    "nationality": (row.get("Nationalité") or "").strip() or None,
                    "instagram": insta_handle,
                    "posts": posts_int,
                    "bio": (row.get("Info en bio") or "").strip() or None,
                }
            )
        self.final_results = results

    def _load_csv_if_exists(self, path: str) -> None:
        if os.path.exists(path):
            self._load_csv(path)
            self._set_status(f"CSV chargé: {path}")

    def _load_csv(self, path: str) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        def _norm_col(s: str) -> str:
            try:
                import unicodedata

                s2 = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode("ascii")
            except Exception:
                s2 = str(s or "")
            s2 = s2.strip().lower()
            s2 = s2.replace("'", "").replace('"', "")
            s2 = s2.replace(" ", "").replace("-", "").replace("_", "")
            return s2

        def _get_cell(row: dict, col: str) -> str:
            if not row:
                return ""
            # Fast path: exact match
            v = row.get(col)
            if v is not None and v != "":
                return v
            # Robust path: normalized header matching + common variants
            norm_row = {_norm_col(k): k for k in row.keys()}
            wants = {_norm_col(col)}
            # tolerate singular/plural for "post(s)"
            wants.add(_norm_col(col.replace("posts", "post")))
            wants.add(_norm_col(col.replace("post", "posts")))
            for w in wants:
                k = norm_row.get(w)
                if k is not None:
                    return row.get(k, "") or ""
            return ""

        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            # Detect delimiter from header line (legacy exports use ';' on FR locale)
            head = f.readline()
            delim = ";" if head.count(";") >= head.count(",") else ","
            f.seek(0)
            reader = csv.DictReader(f, delimiter=delim)
            for row in reader:
                values = [_get_cell(row, col) for col in CSV_COLUMNS]
                self.tree.insert("", "end", values=values)

    def on_load_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Charger un CSV",
            filetypes=[("CSV", "*.csv")],
            initialdir=ROOT_DIR,
        )
        if not path:
            return
        self._load_csv(path)
        self.current_csv_path = path
        self._set_status(f"CSV chargé: {path}")

    def on_add_manual(self) -> None:
        win = tk.Toplevel(self)
        win.title("Ajouter un sportif")
        win.geometry("800x560")
        entries: Dict[str, tk.Entry] = {}
        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)

        for i, col in enumerate(CSV_COLUMNS):
            ttk.Label(frame, text=col).grid(row=i, column=0, sticky="w", pady=2)
            e = ttk.Entry(frame, width=80)
            e.grid(row=i, column=1, sticky="we", pady=2)
            entries[col] = e

        entries["Date d'ajout"].insert(0, now_str())
        frame.columnconfigure(1, weight=1)

        def save_manual() -> None:
            row = {c: entries[c].get().strip() for c in CSV_COLUMNS}
            if not row["Age"]:
                row["Age"] = calc_age_human(row.get("Date de naissance", "")) or calc_age(row.get("Date de naissance", ""))
            self.tree.insert("", "end", values=[row.get(c, "") for c in CSV_COLUMNS])
            self._persist_table_changes(status="Ligne ajoutée.")
            win.destroy()

        ttk.Button(frame, text="Ajouter", command=save_manual).grid(row=len(CSV_COLUMNS) + 1, column=1, sticky="e", pady=10)

    def _persist_table_changes(self, status: Optional[str] = None) -> None:
        # Pendant une recherche, éviter l'auto-sauvegarde à chaque clic/édition (coûteux).
        # On marque "dirty" et on persiste une seule fois en fin de recherche.
        if getattr(self, "_search_in_progress", False):
            self._autosave_dirty_during_search = True
            self._build_final_results_from_table()
            if status:
                self._set_status(status, step=True)
            return
        self._autosave_csv(self._current_search_text() or "manuel", timing_step=True)
        self._build_final_results_from_table()
        if status:
            self._set_status(status, step=True)

    def _on_tree_select_all(self, _event: Optional[tk.Event] = None) -> str:
        """Ctrl+A / Cmd+A : sélectionner toutes les lignes du tableau (puis suppression avec Suppr ou −)."""
        rows = self.tree.get_children()
        if not rows:
            return "break"
        self.tree.selection_set(rows)
        self.tree.focus(rows[0])
        self.tree.see(rows[0])
        return "break"

    def on_delete_selected_rows(self) -> None:
        selected = list(self.tree.selection())
        if not selected:
            messagebox.showinfo(APP_TITLE, "Sélectionnez au moins une ligne à supprimer.")
            return
        count = len(selected)
        if not messagebox.askyesno(APP_TITLE, f"Supprimer {count} ligne(s) sélectionnée(s) ?"):
            return
        for iid in selected:
            try:
                self.tree.delete(iid)
            except Exception:
                pass
        self._persist_table_changes(status=f"{count} ligne(s) supprimée(s).")

    def _edit_cell(self, event: tk.Event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = self.tree.identify_row(event.y)
        col_id = self.tree.identify_column(event.x)
        if not row_id or not col_id:
            return
        col_idx = int(col_id.replace("#", "")) - 1
        x, y, w, h = self.tree.bbox(row_id, col_id)
        value = self.tree.item(row_id, "values")[col_idx]

        entry = ttk.Entry(self.tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, value)
        entry.focus_set()

        def save_edit(_event=None) -> None:
            values = list(self.tree.item(row_id, "values"))
            values[col_idx] = entry.get()
            self.tree.item(row_id, values=values)
            entry.destroy()
            self._persist_table_changes(status="Ligne modifiée.")

        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)

    def _maybe_open_instagram(self, event: tk.Event) -> None:
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        col_id = self.tree.identify_column(event.x)
        col_idx = int(col_id.replace("#", "")) - 1
        if CSV_COLUMNS[col_idx] != "Instagram":
            return
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
        url = self.tree.item(row_id, "values")[col_idx]
        if isinstance(url, str) and url.startswith("http"):
            webbrowser.open(url)

    def on_help(self) -> None:
        text = (
            "Guide rapide\n\n"
            "1) Entrez sport + club/ville puis cliquez Rechercher.\n"
            "2) Masculin / Féminin (menu) : oriente la recherche d’effectif (site officiel, Google puis DDGS) "
            "vers l’équipe correspondante ; le sport et le club viennent des champs que vous saisissez.\n"
            "3) Saison : ex. 2025-2026 ou 2025-2026 du Paris Saint-Germain — cible "
            "la page Wikipédia de saison et filtre mieux l’effectif.\n"
            "4) Élargissez les colonnes en glissant les bords des en-têtes. "
            "5) Éditez les cellules en double-cliquant une cellule.\n"
            "6) Exportez en CSV.\n\n"
            "Si Instagram limite les recherches:\n"
            "- Réessayez plus tard.\n"
            "- Utilisez un VPN.\n\n"
            "Astuce: Activez les logs pour diagnostiquer une recherche."
        )
        messagebox.showinfo("Aide", text)


def check_chrome_setup() -> None:
    webdrv, webdrv_exc, _ = ensure_selenium()
    if webdrv is None:
        return
    try:
        options = webdrv.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdrv.Chrome(options=options)
        driver.quit()
    except webdrv_exc:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            APP_TITLE,
            "Chrome ou ChromeDriver non détecté.\n"
            "Installez Google Chrome pour activer la recherche Selenium si nécessaire.",
        )
        root.destroy()


def acquire_single_instance() -> bool:
    global _single_instance_socket
    _single_instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _single_instance_socket.bind(("127.0.0.1", 53891))
        return True
    except OSError:
        return False


def main() -> None:
    ensure_app_folders()
    app = AthleteApp()
    app.mainloop()


if __name__ == "__main__":
    main()
