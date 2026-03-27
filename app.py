import csv
import datetime as dt
import os
import threading
import time
import traceback
import tkinter as tk
import socket
import re
import html
import json
from dataclasses import dataclass
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional
import webbrowser
from urllib.parse import quote_plus
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
BASE_DIR = r"C:/Sportifs"
DATA_DIR = os.path.join(BASE_DIR, "Data")
LOG_DIR = os.path.join(BASE_DIR, "Logs")
_single_instance_socket = None
# Instagram login removed (web-only)
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

def ensure_app_folders() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


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


def count_recent_posts(post_dates: List[dt.datetime]) -> int:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=90)
    return sum(1 for d in post_dates if d >= cutoff)


def append_log(text: str) -> None:
    ensure_app_folders()
    log_path = os.path.join(LOG_DIR, f"app_{dt.date.today().isoformat()}.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{dt.datetime.now().isoformat(sep=' ', timespec='seconds')}] {text}\n")
    if logger is not None:
        try:
            logger.add(log_path, rotation="2 MB", retention=5)
            logger.info(text)
        except Exception:
            pass
    if LOG_SINK is not None:
        try:
            LOG_SINK(text)
        except Exception:
            pass


LOG_SINK = None


class RetryManager:
    retries = 3
    backoff = [2, 5, 10]

    def run(self, label: str, fn):
        last_exc = None
        for attempt in range(1, self.retries + 1):
            try:
                append_log(f"[RetryManager] {label} tentative {attempt}/{self.retries}")
                return fn()
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
                last_exc = e
                wait_s = self.backoff[min(attempt - 1, len(self.backoff) - 1)]
                append_log(f"[RetryManager] {label} échec: {e} (attente {wait_s}s)")
                time.sleep(wait_s)
        raise last_exc


RETRY = RetryManager()
UA = UserAgent() if UserAgent is not None else None


def http_get(url: str, timeout: int = 20) -> requests.Response:
    ua = UA.random if UA is not None else (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    headers = {"User-Agent": ua}
    return requests.get(url, headers=headers, timeout=timeout)


def wikidata_search_entity(name: str) -> Optional[str]:
    url = (
        "https://www.wikidata.org/w/api.php?"
        f"action=wbsearchentities&search={quote_plus(name)}&language=en&format=json"
    )

    def _do():
        r = http_get(url)
        r.raise_for_status()
        data = r.json()
        if data.get("search"):
            return data["search"][0].get("id")
        return None

    return RETRY.run(f"Wikidata search {name}", _do)


def wikidata_get_entity(qid: str) -> dict:
    url = (
        "https://www.wikidata.org/w/api.php?"
        f"action=wbgetentities&ids={quote_plus(qid)}&props=claims|labels&languages=en&format=json"
    )

    def _do():
        r = http_get(url)
        r.raise_for_status()
        return r.json()

    return RETRY.run(f"Wikidata entity {qid}", _do)


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


def wikipedia_find_page_title(search_query: str) -> Optional[str]:
    search_url = (
        "https://en.wikipedia.org/w/api.php?"
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

    return RETRY.run(f"Wikipedia search title {search_query}", _do)


def wikipedia_extract_names_from_page(title: str, limit: int = 60, section_hints: Optional[List[str]] = None) -> List[str]:
    """
    Best-effort extraction of player-like names from an English Wikipedia page.
    Uses BeautifulSoup when available; otherwise falls back to regex.
    """
    url = f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"

    def _do():
        r = http_get(url, timeout=30)
        r.raise_for_status()
        return r.text

    html_text = RETRY.run(f"Wikipedia html {title}", _do)
    names: List[str] = []
    seen = set()

    def _is_likely_player_name(name: str) -> bool:
        n = (name or "").strip()
        if not n:
            return False
        lower = n.lower()
        # Common non-player entities seen in squad tables (clubs, sections, metadata).
        blocked_tokens = [
            "fc", "cf", "ac", "sc", "inter", "bayern", "sporting", "eintracht", "saint-germain",
            "paris", "milan", "frankfurt", "são paulo", "sao paulo", "loan", "captain", "manager",
            "coach", "league", "cup", "women", "youth", "reserve", "academy",
        ]
        if any(tok in lower for tok in blocked_tokens):
            return False
        # Player-like: 2-3 words, letters/apostrophes/hyphen/accents.
        if not re.match(r"^[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]+){1,2}$", n):
            return False
        return True

    def _add(name: str) -> None:
        n = (name or "").strip()
        if not n:
            return
        if not _is_likely_player_name(n):
            return
        key = n.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(n)

    hints = [h.strip().lower() for h in (section_hints or []) if (h or "").strip()]
    if BeautifulSoup is not None:
        soup = BeautifulSoup(html_text, "html.parser")
        # Try to narrow to a specific section (e.g. "Current squad") to avoid historical players/staff.
        scoped_tables = []
        if hints:
            for h in hints:
                # Wikipedia section ids are often like "Current_squad"
                sec_id = h.replace(" ", "_")
                anchor = soup.find(id=re.compile(rf"^{re.escape(sec_id)}$", re.IGNORECASE))
                if anchor is not None:
                    # h2/h3 headline container
                    headline = anchor
                    for _ in range(3):
                        if headline and headline.name in ("h2", "h3", "h4"):
                            break
                        headline = headline.parent
                    node = headline
                    # walk forward until next heading; collect wikitable
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
                            # usually first table is enough for names
                            break
                if scoped_tables:
                    break

        tables = scoped_tables if scoped_tables else soup.select("table.wikitable")
        for tbl in tables[:6]:
            # Prefer extracting from a "Player/Name" column when present.
            player_col_idx = None
            header_cells = tbl.select("tr th")
            for idx, th in enumerate(header_cells[:12]):
                htxt = (th.get_text(" ", strip=True) or "").lower()
                if any(k in htxt for k in ("player", "name", "squad")):
                    player_col_idx = idx
                    break

            rows = tbl.select("tr")
            for tr in rows:
                cells = tr.find_all(["th", "td"])
                if not cells:
                    continue
                # First choice: dedicated player column.
                candidate_links = []
                if player_col_idx is not None and player_col_idx < len(cells):
                    candidate_links = cells[player_col_idx].select("a[href^='/wiki/']")
                # Fallback: first data cell links.
                if not candidate_links and len(cells) > 1:
                    candidate_links = cells[1].select("a[href^='/wiki/']")
                # Last fallback: any link in row.
                if not candidate_links:
                    candidate_links = tr.select("a[href^='/wiki/']")

                for a in candidate_links:
                    txt = (a.get_text() or "").strip()
                    if not txt or ":" in txt:
                        continue
                    _add(txt)
                    if len(names) >= limit:
                        return names
    # Fallback regex over page text (less precise).
    for m in re.findall(r"\b[A-Z][A-Za-zÀ-ÿ'\-]+(?:\s+[A-Z][A-Za-zÀ-ÿ'\-]+){1,2}\b", html_text):
        _add(m)
        if len(names) >= limit:
            break
    return names


def wikidata_team_players(team_name: str, limit: int = 30, season_start_year: Optional[int] = None) -> List[dict]:
    """
    Best-effort roster-like list from Wikidata.
    Returns list of dicts: name, birth_date, nationality, instagram.
    """
    team_qid = CLUB_WIKIDATA_QIDS.get((team_name or "").strip().lower())
    if not team_qid:
        team_qid = wikidata_search_entity(team_name)
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
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
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
    Best-effort Wikipedia parse: birth date + nationality from infobox/lead.
    Returns partial dict with keys birth_date, nationality, bio.
    """
    # Resolve a reliable page title first (prevents many 404 due to exact-title mismatch).
    search_url = (
        "https://en.wikipedia.org/w/api.php?"
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
        "https://en.wikipedia.org/w/api.php?"
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
    if "instagram.com/" in s:
        s = s.split("instagram.com/", 1)[1]
    s = s.split("?", 1)[0].split("#", 1)[0].strip("/")
    if not s:
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
    return os.path.join(APP_DIR, f"{query_part}_{stamp}.csv")


def generate_export_filename(search_text: str) -> str:
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = sanitize_filename(search_text or "export")
    return f"{base}_{stamp}.csv"


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
        self.win.grab_set()

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
            self.win.grab_release()
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
        self.win.grab_set()

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
            self.win.grab_release()
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
    min_followers: int
    age_min: Optional[int]
    age_max: Optional[int]
    max_profiles: int = 20

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
        self._active_busy_dialog: Optional[BusyDialog] = None
        self._instagram_prompt_done = False
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

    def _start_background_checks(self) -> None:
        t = threading.Thread(target=self._check_chrome_setup_non_blocking, daemon=True)
        t.start()

    # Startup Instagram init removed (instaloader not used).

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
        self.sport_entry = ttk.Entry(form_row, textvariable=self.sport_var, width=18)
        self.sport_entry.grid(row=0, column=1, padx=6, sticky="ew")
        self.sport_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(form_row, text="Club").grid(row=0, column=2, sticky="w")
        self.club_var = tk.StringVar(value=DEFAULT_CLUB)
        self.club_entry = ttk.Entry(form_row, textvariable=self.club_var, width=22)
        self.club_entry.grid(row=0, column=3, padx=6, sticky="ew")
        self.club_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(form_row, text="Ville").grid(row=0, column=4, sticky="w")
        self.ville_var = tk.StringVar(value=DEFAULT_VILLE)
        self.ville_entry = ttk.Entry(form_row, textvariable=self.ville_var, width=18)
        self.ville_entry.grid(row=0, column=5, padx=6, sticky="ew")
        self.ville_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(form_row, text="Saison").grid(row=0, column=6, sticky="w")
        self.saison_var = tk.StringVar(value=default_season_label())
        self.saison_entry = ttk.Entry(form_row, textvariable=self.saison_var, width=12)
        self.saison_entry.grid(row=0, column=7, padx=6, sticky="ew")
        self.saison_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(form_row, text="Abonnés min:").grid(row=0, column=8, sticky="e")
        self.min_followers_var = tk.StringVar(value="5000")
        ttk.Entry(form_row, textvariable=self.min_followers_var, width=10).grid(row=0, column=9, padx=6)

        ttk.Label(form_row, text="Age min:").grid(row=0, column=10, sticky="e")
        self.age_min_var = tk.StringVar()
        ttk.Entry(form_row, textvariable=self.age_min_var, width=7).grid(row=0, column=11, padx=6)

        ttk.Label(form_row, text="Age max:").grid(row=0, column=12, sticky="e")
        self.age_max_var = tk.StringVar()
        ttk.Entry(form_row, textvariable=self.age_max_var, width=7).grid(row=0, column=13, padx=6)

        self.search_btn = ttk.Button(actions_row, text="🔍", width=3, command=self.on_search)
        self.search_btn.grid(row=0, column=0, padx=(0, 8), sticky="w")

        ttk.Button(actions_row, text="Connexion Instagram", command=self.on_connect_instagram).grid(row=0, column=1, padx=4, sticky="w")
        ttk.Button(actions_row, text="Charger CSV", command=self.on_load_csv).grid(row=0, column=2, padx=4, sticky="w")
        ttk.Button(actions_row, text="Exporter en CSV", command=self.on_export_csv).grid(row=0, column=3, padx=4, sticky="w")
        ttk.Button(actions_row, text="Ajouter manuellement", command=self.on_add_manual).grid(row=0, column=4, padx=4, sticky="w")
        ttk.Button(actions_row, text="Aide", command=self.on_help).grid(row=0, column=5, padx=4, sticky="w")

        self.toggle_logs_btn = ttk.Button(actions_row, text="Afficher logs", command=self._toggle_logs)
        self.toggle_logs_btn.grid(row=0, column=6, padx=10, sticky="w")

        # Stabilize form layout so input fields remain visible.
        form_row.columnconfigure(1, weight=1, minsize=150)
        form_row.columnconfigure(3, weight=1, minsize=170)
        form_row.columnconfigure(5, weight=1, minsize=140)
        form_row.columnconfigure(7, weight=0, minsize=90)

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

        for col in CSV_COLUMNS:
            self.tree.heading(col, text=col)
            width = 140
            if col in ("Autres informations", "Info en bio", "Instagram"):
                width = 220
            self.tree.column(col, width=width, anchor="w")

        self.tree.bind("<Double-1>", self._edit_cell)
        self.tree.bind("<ButtonRelease-1>", self._maybe_open_instagram)

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

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        append_log(text)
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
        # instaloader removed; keep a stable info indicator.
        self.ig_status_var.set("Instagram: Web-only (pas de session)")
        self.ig_status_label.configure(foreground="#2a4b8d")

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
            min_followers = int(self.min_followers_var.get().strip() or "0")
            age_min = int(self.age_min_var.get().strip()) if self.age_min_var.get().strip() else None
            age_max = int(self.age_max_var.get().strip()) if self.age_max_var.get().strip() else None
        except ValueError:
            messagebox.showerror(APP_TITLE, "Filtres invalides. Utilisez des nombres.")
            return None
        return SearchFilters(
            sport=sport,
            club=club,
            ville=ville,
            saison=saison,
            saison_start_year=saison_start_year,
            min_followers=min_followers,
            age_min=age_min,
            age_max=age_max,
        )

    def on_search(self) -> None:
        filters = self._get_filters()
        if not filters:
            return
        # New cancel token per search
        self._search_cancel_event = threading.Event()
        self._search_cancel_requested = False
        self.search_btn.configure(state="disabled")
        self._set_status("Recherche en cours...")
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
            append_log("Annulation demandée par l'utilisateur.")
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
                        "Aucun profil trouvé. Essayez Abonnés min=0 ou précisez Club/Ville."
                    ),
                )
            else:
                self.after(0, lambda: self._add_rows(rows))
                self.after(0, lambda: self._set_status(f"{len(rows)} profils trouvés."))
                self.after(0, lambda: self._autosave_csv(filters.query))
                self.after(0, lambda: self._build_final_results_from_table())
        except SearchCancelled:
            self.after(0, lambda: self._set_status("Recherche annulée."))
        except Exception as exc:
            append_log(traceback.format_exc())
            self.after(0, lambda: messagebox.showerror(APP_TITLE, self._format_user_error(exc)))
        finally:
            self.after(0, self._close_search_dialog)
            self.after(0, lambda: self.search_btn.configure(state="normal"))

    def _search_wikidata_roster(self, filters: SearchFilters) -> List[Dict[str, str]]:
        club = (filters.club or "").strip()
        sport = (filters.sport or "").strip().lower()
        ville = (filters.ville or "").strip()
        if not club:
            return []
        if sport not in ("foot", "football"):
            return []

        try:
            if filters.saison_start_year is not None:
                self.after(
                    0,
                    lambda y=filters.saison_start_year: self._set_status(
                        f"Wikidata: récupération de la liste joueurs (saison {y}-{y+1})…"
                    ),
                )
            else:
                self.after(0, lambda: self._set_status("Wikidata: récupération de la liste joueurs (période récente)…"))
            players = wikidata_team_players(
                club,
                limit=filters.max_profiles * 3,
                season_start_year=filters.saison_start_year,
            )
            append_log(f"Wikidata: {len(players)} joueurs candidats reçus.")
        except Exception as e:
            append_log(f"Wikidata roster échoué: {e}")
            # Fallback when SPARQL is down: try extract names from Wikipedia squad/season pages.
            try:
                self.after(0, lambda: self._set_status("Fallback: Wikipedia (page effectif/saison)…"))
                season_text = filters.saison or ""
                title = None
                # Club alias to improve Wikipedia target (psg -> full club name).
                club_for_wiki = club.strip()
                if club_for_wiki.lower() in ("psg", "paris sg", "paris-sg"):
                    club_for_wiki = "Paris Saint-Germain F.C."

                # Prefer an exact season page title search (reduces “historical players” pages).
                start = filters.saison_start_year
                if start is not None:
                    end = start + 1
                    season_labels = [f"{start}–{end}", f"{start}-{end}", f"{start}–{end} season"]
                    for lab in season_labels:
                        title = wikipedia_find_page_title(f"{lab} {club_for_wiki} season")
                        if title:
                            break

                if not title:
                    q_parts = [club_for_wiki, "current squad", season_text]
                    search_query = " ".join(x for x in q_parts if x).strip()
                    title = wikipedia_find_page_title(search_query)
                if not title:
                    # Alternative query more PSG-friendly
                    title = wikipedia_find_page_title(f"{club_for_wiki} season {season_text}".strip())
                if not title:
                    append_log("Fallback Wikipedia: aucune page trouvée (titre introuvable).")
                    return []
                append_log(f"Fallback Wikipedia: page = {title}")
                names = wikipedia_extract_names_from_page(
                    title,
                    limit=filters.max_profiles * 4,
                    section_hints=["Current squad", "Squad"],
                )
                append_log(f"Fallback Wikipedia: {len(names)} noms extraits.")
                if not names:
                    return []
                # Build rows similarly to web-first pipeline but without Selenium.
                rows: List[Dict[str, str]] = []
                for idx, name in enumerate(names, start=1):
                    self._raise_if_cancelled()
                    if len(rows) >= filters.max_profiles:
                        break
                    row = {c: "" for c in CSV_COLUMNS}
                    nom, prenom = self._split_name(name)
                    row["Nom"] = nom
                    row["Prénom"] = prenom
                    row["Date d'ajout"] = now_str()
                    row["Sport"] = filters.sport
                    row["Club"] = club
                    row["Ville"] = ville
                    row["Autres informations"] = f"Source: Wikipedia ({title})"
                    # Enrich Wikipedia -> Wikidata (best-effort)
                    try:
                        ps = self._build_player_struct(name=name, ig_username=None)
                        if ps.get("birth_date"):
                            row["Date de naissance"] = ps["birth_date"]
                            row["Age"] = str(ps.get("age") or "")
                        if ps.get("nationality"):
                            row["Nationalité"] = ps["nationality"]
                        if ps.get("bio"):
                            row["Info en bio"] = ps["bio"]
                        if ps.get("instagram"):
                            row["Instagram"] = ps["instagram"]
                    except Exception as ex2:
                        append_log(f"Fallback Wikipedia enrichissement échoué ({name}): {ex2}")

                    # Age filters (same behavior as roster)
                    if filters.age_min is not None and row["Age"].isdigit() and int(row["Age"]) < filters.age_min:
                        append_log(f"Fallback Wikipedia: rejet {name} (âge {row['Age']} < min {filters.age_min})")
                        continue
                    if filters.age_max is not None and row["Age"].isdigit() and int(row["Age"]) > filters.age_max:
                        append_log(f"Fallback Wikipedia: rejet {name} (âge {row['Age']} > max {filters.age_max})")
                        continue

                    rows.append(row)
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
        rejected = 0
        for p in players:
            self._raise_if_cancelled()
            if len(rows) >= filters.max_profiles:
                break
            analyzed += 1
            name = p.get("name") or ""
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
            row["Age"] = calc_age(row["Date de naissance"]) if row["Date de naissance"] else ""
            row["Nationalité"] = p.get("nationality") or ""
            row["Instagram"] = (f"https://instagram.com/{ig[1:]}" if ig and ig.startswith("@") else (ig or ""))
            row["Info en bio"] = ""
            row["Autres informations"] = "Source: Wikidata roster"
            row["Nombre de points"] = ""
            row["Niveau Barème"] = ""
            row["Priorité"] = ""

            # Age filters: reject and log (keep going).
            if filters.age_min is not None and row["Age"].isdigit() and int(row["Age"]) < filters.age_min:
                rejected += 1
                append_log(f"Wikidata: rejet {name} (âge {row['Age']} < min {filters.age_min})")
                continue
            if filters.age_max is not None and row["Age"].isdigit() and int(row["Age"]) > filters.age_max:
                rejected += 1
                append_log(f"Wikidata: rejet {name} (âge {row['Age']} > max {filters.age_max})")
                continue

            # Optional Wikipedia bio (best effort). Keep logs concise.
            try:
                wiki = wikipedia_extract_player_data(name)
                if wiki.get("bio"):
                    row["Info en bio"] = wiki["bio"]
            except Exception as e:
                append_log(f"Wikipedia bio indisponible ({name}): {e}")

            # Followers/posts via web scraping is best-effort; keep empty if fails.
            rows.append(row)
            self.after(
                0,
                lambda a=analyzed, r=len(rows), n=name: self._update_search_dialog(
                    a, f"Wikidata: {n}", analyzed=a, retained=r
                ),
            )

        if rejected:
            append_log(f"Wikidata: {rejected} joueurs rejetés par filtres (âge).")
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
                "- Mettre Abonnés min = 0 si vous n'êtes pas connecté à Instagram.\n"
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
            append_log("Recherche web-first: sport vide, retour vide.")
            return []
        area = " ".join(x for x in [club, ville] if x).strip()
        if not area:
            area = sport

        # Primary: Wikipedia squad/season page (structured) => names
        player_names: List[str] = []
        try:
            self.after(
                0,
                lambda: self._update_search_dialog(
                    0, "Recherche joueurs: Wikipedia (effectif/saison)…", analyzed=0, retained=0
                ),
            )
            self._raise_if_cancelled()
            season_text = (filters.saison or "").strip()

            club_for_wiki = club.strip() or area
            if club_for_wiki.lower() in ("psg", "paris sg", "paris-sg"):
                club_for_wiki = "Paris Saint-Germain F.C."

            title = None
            start = filters.saison_start_year
            if start is not None:
                end = start + 1
                # Try typical Wikipedia season page titles first.
                for lab in (f"{start}–{end}", f"{start}-{end}"):
                    title = wikipedia_find_page_title(f"{lab} {club_for_wiki} season")
                    if title:
                        break
            if not title:
                title = wikipedia_find_page_title(f"{club_for_wiki} current squad {season_text}".strip())
            if not title:
                title = wikipedia_find_page_title(f"{club_for_wiki} squad".strip())

            if title:
                append_log(f"Recherche joueurs Wikipedia: page = {title}")
                player_names = wikipedia_extract_names_from_page(
                    title,
                    limit=filters.max_profiles * 4,
                    section_hints=["Current squad", "Squad"],
                )
                append_log(f"Recherche joueurs Wikipedia: {len(player_names)} noms extraits.")
        except Exception as e:
            append_log(f"Recherche joueurs Wikipedia échouée: {e}")
            player_names = []

        # Secondary: DuckDuckGo via Selenium (last resort) when Wikipedia yields nothing.
        driver = None
        by_cls = None
        webdrv_exc = Exception
        if not player_names:
            webdrv, webdrv_exc, by_cls = ensure_selenium()
            if webdrv is None or by_cls is None:
                append_log("Recherche joueurs: Selenium indisponible et Wikipedia vide.")
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

            total_candidates = min(len(player_names), filters.max_profiles * 2)
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
                row["Autres informations"] = "Source: Wikipedia roster" if title else "Source: Web roster"
                row["Priorité"] = ""

                # Step: Wikipedia -> Wikidata (always continue)
                player_struct = self._build_player_struct(name=name, ig_username=username)
                # Apply structured fields back to CSV row
                if player_struct.get("birth_date"):
                    row["Date de naissance"] = player_struct["birth_date"]
                    row["Age"] = str(player_struct.get("age") or "")
                if player_struct.get("nationality"):
                    row["Nationalité"] = player_struct["nationality"]
                if player_struct.get("bio"):
                    row["Info en bio"] = player_struct["bio"]
                if player_struct.get("instagram"):
                    row["Instagram"] = player_struct["instagram"]

                # Instagram enrichment removed (web-only). Keep nullable.

                # Si on n'a pas pu enrichir via Instagram, on garde quand même le profil en mode Web,
                # et on n'applique pas le filtre "abonnés min" (valeur inconnue).
                if row["Nombre d'abonnés"]:
                    followers = int(row["Nombre d'abonnés"] or "0")
                    if followers < filters.min_followers:
                        continue

                rows.append(row)
                self.after(
                    0,
                    lambda i=idx, a=analyzed, r=len(rows), n=name: self._update_search_dialog(
                        i, f"Profil retenu: {n}", analyzed=a, retained=r
                    ),
                )
            return rows
        finally:
            try:
                if driver is not None:
                    driver.quit()
            except Exception:
                pass

    def _build_player_struct(self, name: str, ig_username: Optional[str]) -> dict:
        """Build final player structure with Wikipedia -> Wikidata fallback. Never raises."""
        out = {
            "name": name,
            "birth_date": None,
            "age": None,
            "nationality": None,
            "instagram": normalize_instagram_handle(ig_username) if ig_username else None,
            "posts": None,
            "bio": None,
        }
        # Wikipedia (best effort)
        try:
            wiki = wikipedia_extract_player_data(name)
            out["bio"] = wiki.get("bio") or out["bio"]
            # birth_date/nationality not always provided by summary
        except Exception as e:
            append_log(f"Wikipedia échoué pour {name}: {e}")

        # Wikidata fallback
        try:
            qid = wikidata_search_entity(name)
            if qid:
                wd = wikidata_get_entity(qid)
                ent = wd.get("entities", {}).get(qid, {})
                bd = wikidata_claim_string(ent, "P569")
                nat = None
                # Nationality is entity reference (P27). Keep it simple: try label if present.
                try:
                    p27 = ent.get("claims", {}).get("P27")
                    if p27:
                        nid = p27[0]["mainsnak"]["datavalue"]["value"]["id"]
                        nent = wikidata_get_entity(nid).get("entities", {}).get(nid, {})
                        nat = (nent.get("labels", {}).get("en", {}) or {}).get("value")
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
        except Exception as e:
            append_log(f"Wikidata échoué pour {name}: {e}")

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
            search_query = f"site:instagram.com {query} athlete"
            url = f"https://duckduckgo.com/?q={search_query}"
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
                row["Autres informations"] = "Source: fallback web (DuckDuckGo)"
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
        search_query = f"site:instagram.com {query} athlete"
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
            row["Autres informations"] = "Source: fallback web HTTP (DuckDuckGo)"
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
        # DuckDuckGo HTML often embeds target URLs in "uddg=<urlencoded>".
        # Important: stop at '&' to avoid keeping '&rut=...' tracking tokens.
        for m in re.findall(r"uddg=([^&\"'\s>]+)", html_text):
            try:
                decoded = requests.utils.unquote(m)
            except Exception:
                decoded = m
            decoded = html.unescape(decoded)
            if "instagram.com/" in decoded:
                out.append(decoded)
        # Fallback direct URL extraction.
        for m in re.findall(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9._/]+", html_text):
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

    def on_connect_instagram(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            "Connexion Instagram directe désactivée.\n\n"
            "L'application fonctionne en mode Web (Wikidata/Wikipedia + recherche Instagram).",
        )

    def on_test_instagram(self) -> None:
        messagebox.showinfo(
            APP_TITLE,
            "Test Instagram direct désactivé.\n"
            "La recherche continue via le Web même si Instagram limite l'accès.",
        )

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
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, delimiter=";")
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
        if v:
            m = re.search(r"\d+", v)
            if m:
                return f"{int(m.group(0))} ans"
        computed = calc_age(birth_date or "")
        if computed and computed.isdigit():
            return f"{int(computed)} ans"
        return ""

    def _format_row_for_export(self, row: Dict[str, str]) -> Dict[str, str]:
        out = {c: (row.get(c, "") or "") for c in CSV_COLUMNS}
        out["Date d'ajout"] = self._format_date_fr(out.get("Date d'ajout", ""))
        out["Date de naissance"] = self._format_date_fr(out.get("Date de naissance", ""))
        out["Age"] = self._format_age_human(out.get("Age", ""), out.get("Date de naissance", ""))

        insta = (out.get("Instagram", "") or "").strip()
        if insta.startswith("http"):
            handle = normalize_instagram_handle(insta)
            out["Instagram"] = handle[1:] if handle and handle.startswith("@") else (handle or insta)

        return out

    def _write_html_preview(self, csv_path: str) -> Optional[str]:
        """
        Create a styled HTML preview next to CSV (colors/layout), since CSV itself cannot store colors.
        """
        try:
            rows = [self._format_row_for_export(r) for r in self._tree_rows()]
            if not rows:
                return None
            html_path = os.path.splitext(csv_path)[0] + ".html"
            css = (
                "body{font-family:Segoe UI,Arial,sans-serif;margin:16px;background:#f6f8fb;color:#1f2937;}"
                "h1{font-size:18px;margin:0 0 10px 0;color:#0f172a;}"
                "table{border-collapse:collapse;width:100%;background:#fff;}"
                "th,td{border:1px solid #dbe3ef;padding:6px 8px;font-size:12px;vertical-align:top;}"
                "th{background:#1d4ed8;color:#fff;position:sticky;top:0;}"
                "tr:nth-child(even){background:#f8fbff;}"
            )
            with open(html_path, "w", encoding="utf-8") as f:
                f.write("<!doctype html><html><head><meta charset='utf-8'>")
                f.write(f"<style>{css}</style></head><body>")
                f.write(f"<h1>{html.escape(APP_TITLE)} - Export</h1>")
                f.write("<table><thead><tr>")
                for col in CSV_COLUMNS:
                    f.write(f"<th>{html.escape(col)}</th>")
                f.write("</tr></thead><tbody>")
                for row in rows:
                    f.write("<tr>")
                    for col in CSV_COLUMNS:
                        f.write(f"<td>{html.escape(str(row.get(col, '') or ''))}</td>")
                    f.write("</tr>")
                f.write("</tbody></table></body></html>")
            return html_path
        except Exception as e:
            append_log(f"Prévisualisation HTML échouée: {e}")
            return None

    def _autosave_csv(self, query: str) -> None:
        if not self.current_csv_path:
            self.current_csv_path = generate_search_csv_path(query)
        self._write_csv(self.current_csv_path)
        html_path = self._write_html_preview(self.current_csv_path)
        self._set_status(f"Auto-sauvegarde: {self.current_csv_path}")
        if html_path:
            append_log(f"Aperçu HTML stylisé: {html_path}")

    def on_export_csv(self) -> None:
        suggested_name = generate_export_filename(self._current_search_text())
        path = filedialog.asksaveasfilename(
            title="Exporter en CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=suggested_name,
            initialdir=APP_DIR,
        )
        if not path:
            return
        self._write_csv(path)
        html_path = self._write_html_preview(path)
        self.current_csv_path = path
        # Export JSON alongside CSV
        try:
            self._build_final_results_from_table()
            json_path = os.path.splitext(path)[0] + ".json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(self.final_results, f, ensure_ascii=False, indent=2)
            self._set_status(f"Export JSON: {json_path}")
        except Exception as e:
            append_log(f"Export JSON échoué: {e}")
        self._set_status(f"Exporté: {path}")
        if html_path:
            self._set_status(f"Aperçu HTML: {html_path}")
        messagebox.showinfo(APP_TITLE, f"CSV exporté:\n{path}")

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
        with open(path, "r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                values = [row.get(col, "") for col in CSV_COLUMNS]
                self.tree.insert("", "end", values=values)

    def on_load_csv(self) -> None:
        path = filedialog.askopenfilename(
            title="Charger un CSV",
            filetypes=[("CSV", "*.csv")],
            initialdir=APP_DIR,
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
                row["Age"] = calc_age(row.get("Date de naissance", ""))
            self.tree.insert("", "end", values=[row.get(c, "") for c in CSV_COLUMNS])
            self._autosave_csv(self._current_search_text() or "manuel")
            win.destroy()

        ttk.Button(frame, text="Ajouter", command=save_manual).grid(row=len(CSV_COLUMNS) + 1, column=1, sticky="e", pady=10)

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
            "2) Éditez les colonnes directement en double-cliquant une cellule.\n"
            "3) Exportez en CSV.\n\n"
            "Si Instagram limite les recherches:\n"
            "- Réessayez plus tard.\n"
            "- Utilisez un VPN.\n\n"
            "Support: envoi 100€ a RAFA"
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
    if not acquire_single_instance():
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_TITLE, "L'application est déjà lancée.")
        root.destroy()
        return
    app = AthleteApp()
    app.mainloop()


if __name__ == "__main__":
    main()
