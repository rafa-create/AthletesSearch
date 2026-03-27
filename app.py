import csv
import datetime as dt
import os
import threading
import time
import traceback
import tkinter as tk
import socket
import re
from dataclasses import dataclass
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional
import webbrowser
from urllib.parse import quote_plus

instaloader = None
webdriver = None
WebDriverException = Exception
By = None

APP_NAME = "Athletes Searcher"
APP_VERSION = "v01.00.00"
APP_TITLE = f"{APP_NAME} {APP_VERSION}"
MIN_SPLASH_MS = 1200
APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = r"C:/Sportifs"
DATA_DIR = os.path.join(BASE_DIR, "Data")
LOG_DIR = os.path.join(BASE_DIR, "Logs")
SESSION_USER_FILE = os.path.join(DATA_DIR, "instagram_session_user.txt")
_single_instance_socket = None
DEFAULT_IG_USERNAME = "pogo.loc"
DEFAULT_IG_PASSWORD = "Pogo54500/"
DEFAULT_SPORT = "foot"
DEFAULT_VILLE = "paris"
DEFAULT_CLUB = "psg"

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
    global instaloader
    if instaloader is None:
        try:
            import instaloader as _instaloader
            instaloader = _instaloader
        except Exception:
            return None
    return instaloader


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
    def __init__(self, root: tk.Tk, total: int) -> None:
        self.root = root
        self.total = max(1, int(total))
        self.indeterminate = True
        self.win = tk.Toplevel(root)
        self.win.title("Recherche en cours…")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)

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

        self._center()
        self.win.transient(root)
        self.win.grab_set()

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
        ttk.Label(frm, text=message, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 10))
        self.bar = ttk.Progressbar(frm, mode="indeterminate", length=360)
        self.bar.pack(fill="x")

        self._center()
        self.bar.start(12)
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
    min_followers: int
    age_min: Optional[int]
    age_max: Optional[int]
    max_profiles: int = 20

    @property
    def query(self) -> str:
        return " ".join(x for x in [self.sport, self.club, self.ville] if x).strip()


class AthleteApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        ensure_app_folders()
        self.withdraw()

        self.title(APP_TITLE)
        self.geometry("1450x720")
        self.minsize(1200, 640)

        self.current_csv_path: Optional[str] = None
        self._search_dialog: Optional[SearchProgressDialog] = None
        self.instagram_session_user = self._load_session_user()
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
        self.after(120, self._startup_test_instagram_connection)

    def _start_background_checks(self) -> None:
        t = threading.Thread(target=self._check_chrome_setup_non_blocking, daemon=True)
        t.start()

    def _startup_test_instagram_connection(self) -> None:
        """Test Instagram connectivity at startup with loading popup."""
        busy = BusyDialog(self, APP_TITLE, "Vérification connexion Instagram en cours...")

        def worker() -> None:
            connected = False
            try:
                insta = ensure_instaloader()
                if insta is None:
                    connected = False
                else:
                    loader = insta.Instaloader(
                        download_pictures=False,
                        download_video_thumbnails=False,
                        download_videos=False,
                        save_metadata=False,
                        compress_json=False,
                        max_connection_attempts=1,
                    )
                    loaded = self._load_instagram_session(loader)
                    if loaded:
                        test_search = insta.TopSearchResults(loader.context, "football")
                        _ = next(iter(test_search.get_profiles()), None)
                    connected = loaded
            except Exception as e:
                append_log(f"Test Instagram startup échoué: {e}")
                connected = False

            def finish() -> None:
                self._update_instagram_status(connected)
                if connected:
                    self._set_status("Connexion Instagram OK.")
                else:
                    self._set_status("Connexion Instagram non validée.")
                busy.close()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

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

        info_row = ttk.Frame(top)
        info_row.pack(fill="x", pady=(4, 0))

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

        ttk.Label(form_row, text="Abonnés min:").grid(row=0, column=6, sticky="e")
        self.min_followers_var = tk.StringVar(value="5000")
        ttk.Entry(form_row, textvariable=self.min_followers_var, width=10).grid(row=0, column=7, padx=6)

        ttk.Label(form_row, text="Age min:").grid(row=0, column=8, sticky="e")
        self.age_min_var = tk.StringVar()
        ttk.Entry(form_row, textvariable=self.age_min_var, width=7).grid(row=0, column=9, padx=6)

        ttk.Label(form_row, text="Age max:").grid(row=0, column=10, sticky="e")
        self.age_max_var = tk.StringVar()
        ttk.Entry(form_row, textvariable=self.age_max_var, width=7).grid(row=0, column=11, padx=6)

        self.search_btn = ttk.Button(actions_row, text="🔍", width=3, command=self.on_search)
        self.search_btn.grid(row=0, column=0, padx=(0, 8), sticky="w")

        ttk.Button(actions_row, text="Connexion Instagram", command=self.on_connect_instagram).grid(row=0, column=1, padx=4, sticky="w")
        ttk.Button(actions_row, text="Charger CSV", command=self.on_load_csv).grid(row=0, column=2, padx=4, sticky="w")
        ttk.Button(actions_row, text="Exporter en CSV", command=self.on_export_csv).grid(row=0, column=3, padx=4, sticky="w")
        ttk.Button(actions_row, text="Ajouter manuellement", command=self.on_add_manual).grid(row=0, column=4, padx=4, sticky="w")
        ttk.Button(actions_row, text="Aide", command=self.on_help).grid(row=0, column=5, padx=4, sticky="w")

        ttk.Label(
            info_row,
            text="Recherche: Sport obligatoire. Club et/ou Ville recommandés.",
        ).grid(row=0, column=0, sticky="w")

        self.ig_status_var = tk.StringVar(value="Instagram: ○ Non connecté")
        self.ig_status_label = ttk.Label(info_row, textvariable=self.ig_status_var, anchor="e")
        self.ig_status_label.grid(row=0, column=1, sticky="e")
        self._update_instagram_status(bool((self.instagram_session_user or "").strip()))

        # Stabilize form layout so input fields remain visible.
        form_row.columnconfigure(1, weight=1, minsize=150)
        form_row.columnconfigure(3, weight=1, minsize=170)
        form_row.columnconfigure(5, weight=1, minsize=140)
        info_row.columnconfigure(0, weight=1)
        info_row.columnconfigure(1, weight=1, minsize=240)

        status_frame = ttk.Frame(self, padding=(10, 0, 10, 5))
        status_frame.pack(fill="x")
        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")

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

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)
        append_log(text)
        self.update_idletasks()

    def _current_search_text(self) -> str:
        return " ".join(
            x for x in [self.sport_var.get().strip(), self.club_var.get().strip(), self.ville_var.get().strip()] if x
        ).strip()

    def _update_instagram_status(self, connected: bool) -> None:
        if connected:
            user = (self.instagram_session_user or "").strip()
            if user:
                self.ig_status_var.set(f"Instagram: ● Connecté (@{user})")
            else:
                self.ig_status_var.set("Instagram: ● Connecté")
            self.ig_status_label.configure(foreground="#1f8b24")
        else:
            self.ig_status_var.set("Instagram: ○ Non connecté")
            self.ig_status_label.configure(foreground="#8a1c1c")

    def _get_filters(self) -> Optional[SearchFilters]:
        sport = self.sport_var.get().strip()
        club = self.club_var.get().strip()
        ville = self.ville_var.get().strip()
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
            min_followers=min_followers,
            age_min=age_min,
            age_max=age_max,
        )

    def on_search(self) -> None:
        filters = self._get_filters()
        if not filters:
            return
        if not self._ensure_instagram_session_prompt():
            return
        self.search_btn.configure(state="disabled")
        self._set_status("Recherche en cours...")
        if self._search_dialog is not None:
            self._search_dialog.close()
        self._search_dialog = SearchProgressDialog(self, total=filters.max_profiles)
        self._search_dialog.set_indeterminate()
        self._search_dialog.update(0, "Initialisation de la recherche…", analyzed=0, retained=0)
        t = threading.Thread(target=self._run_search, args=(filters,), daemon=True)
        t.start()

    def _run_search(self, filters: SearchFilters) -> None:
        try:
            rows = self._search_players_then_instagram(filters)
            if not rows:
                # Legacy fallback (kept for compatibility)
                rows = self._search_instagram(filters)
            if not rows:
                self.after(0, lambda: self._set_status("Aucun profil trouvé (ou source limitée)."))
            else:
                self.after(0, lambda: self._add_rows(rows))
                self.after(0, lambda: self._set_status(f"{len(rows)} profils trouvés."))
                self.after(0, lambda: self._autosave_csv(filters.query))
        except Exception as exc:
            append_log(traceback.format_exc())
            self.after(0, lambda: messagebox.showerror(APP_TITLE, f"Erreur: {exc}"))
        finally:
            self.after(0, self._close_search_dialog)
            self.after(0, lambda: self.search_btn.configure(state="normal"))

    def _search_players_then_instagram(self, filters: SearchFilters) -> List[Dict[str, str]]:
        webdrv, webdrv_exc, by_cls = ensure_selenium()
        if webdrv is None or by_cls is None:
            return []

        sport = (filters.sport or "").strip()
        club = (filters.club or "").strip()
        ville = (filters.ville or "").strip()
        if not sport:
            return []
        area = " ".join(x for x in [club, ville] if x).strip()
        if not area:
            area = sport

        # Prioritize roster-like queries for better quality player names.
        query_candidates = [
            f"effectif {club} {ville} {sport} 2025 2026",
            f"liste joueurs {club} {ville} {sport}",
            f"{club} roster {sport} players",
            f"effectif {area} {sport}",
        ]

        try:
            options = webdrv.ChromeOptions()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--window-size=1600,1000")
            driver = webdrv.Chrome(options=options)
        except (ModuleNotFoundError, ImportError, webdrv_exc):
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
                    0, "Recherche web des joueurs…", analyzed=0, retained=0
                ),
            )
            player_names: List[str] = []
            for q in query_candidates:
                chunk = self._discover_player_names(driver, by_cls, q, filters.max_profiles * 3)
                for n in chunk:
                    if n not in player_names:
                        player_names.append(n)
                if len(player_names) >= filters.max_profiles * 2:
                    break
            if not player_names:
                return []

            total_candidates = min(len(player_names), filters.max_profiles * 2)
            self.after(
                0,
                lambda t=total_candidates: (
                    self._search_dialog.set_determinate(t) if self._search_dialog is not None else None
                ),
            )

            for idx, name in enumerate(player_names, start=1):
                if len(rows) >= filters.max_profiles:
                    break

                analyzed = idx
                self.after(
                    0,
                    lambda i=idx, n=name, a=analyzed, r=len(rows): self._update_search_dialog(
                        i, f"Recherche Instagram pour {n}…", analyzed=a, retained=r
                    ),
                )
                insta_url = self._find_instagram_profile_for_name(driver, by_cls, name, sport, area)
                if not insta_url:
                    continue

                username = insta_url.rstrip("/").split("/")[-1]
                row = {c: "" for c in CSV_COLUMNS}
                nom, prenom = self._split_name(name)
                row["Nom"] = nom or username
                row["Prénom"] = prenom
                row["Date d'ajout"] = now_str()
                row["Sport"] = sport
                row["Club"] = club or area
                row["Ville"] = ville
                row["Instagram"] = insta_url
                row["Nombre de points"] = ""
                row["Niveau Barème"] = ""
                row["Autres informations"] = "Source: Web roster + Google/DDG -> Instagram profil"
                row["Priorité"] = ""

                self._enrich_row_from_instaloader_username(row, username)

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
            driver.quit()

    def _discover_player_names(self, driver, by_cls, query: str, limit: int) -> List[str]:
        url = f"https://duckduckgo.com/?q={quote_plus(query)}"
        driver.get(url)
        time.sleep(2)
        anchors = driver.find_elements(by_cls.CSS_SELECTOR, "h2 a, a[data-testid='result-title-a']")
        names: List[str] = []
        seen = set()
        for a in anchors:
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
        time.sleep(1.5)
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

    def _enrich_row_from_instaloader_username(self, row: Dict[str, str], username: str) -> None:
        insta = ensure_instaloader()
        if insta is None:
            return
        try:
            loader = insta.Instaloader(
                download_pictures=False,
                download_video_thumbnails=False,
                download_videos=False,
                save_metadata=False,
                compress_json=False,
                max_connection_attempts=1,
            )
            self._load_instagram_session(loader)
            profile = insta.Profile.from_username(loader.context, username)
            row["Nombre d'abonnés"] = str(getattr(profile, "followers", "") or "")
            row["Nombre de posts"] = str(getattr(profile, "mediacount", "") or "")
            row["Info en bio"] = (getattr(profile, "biography", "") or "").strip()
            post_dates = []
            try:
                for i, post in enumerate(profile.get_posts()):
                    if i >= 12:
                        break
                    post_dates.append(post.date_utc.replace(tzinfo=dt.timezone.utc))
            except Exception:
                post_dates = []
            row["Posts 3 derniers mois"] = str(count_recent_posts(post_dates))
        except Exception as e:
            append_log(f"Enrichissement instaloader échoué @{username}: {e}")

    def _close_search_dialog(self) -> None:
        if self._search_dialog is not None:
            self._search_dialog.close()
            self._search_dialog = None

    def _update_search_dialog(self, current: int, message: str, analyzed: int = 0, retained: int = 0) -> None:
        if self._search_dialog is not None:
            self._search_dialog.update(current, message, analyzed=analyzed, retained=retained)

    def _search_instagram(self, filters: SearchFilters) -> List[Dict[str, str]]:
        insta = ensure_instaloader()
        if insta is None:
            raise RuntimeError("instaloader non installé. Installez les dépendances d'abord.")

        loader = insta.Instaloader(
            download_pictures=False,
            download_video_thumbnails=False,
            download_videos=False,
            save_metadata=False,
            compress_json=False,
            max_connection_attempts=1,
        )

        query = filters.query.replace(",", " ").strip()
        terms = [x for x in query.split() if x]
        if not terms:
            return []
        search_term = terms[-1]

        results: List[Dict[str, str]] = []
        count = 0
        try:
            self._load_instagram_session(loader)
            search = insta.TopSearchResults(loader.context, search_term)
            profiles = list(search.get_profiles())
            self.after(
                0,
                lambda t=min(len(profiles), filters.max_profiles): (
                    self._search_dialog.set_determinate(t) if self._search_dialog is not None else None
                ),
            )
        except Exception as e:
            append_log(f"Instagram bloqué / indisponible: {e}")
            self.after(0, lambda: self._set_status("Instagram limité, tentative fallback web..."))
            return self._search_web_fallback(filters)

        for p in profiles:
            if count >= filters.max_profiles:
                break

            username = getattr(p, "username", "") or ""
            self.after(
                0,
                lambda c=count, u=username: self._update_search_dialog(
                    c,
                    f"Analyse du profil @{u}…",
                    analyzed=c,
                    retained=count,
                ),
            )
            self.after(
                0,
                lambda c=count, u=username: self._set_status(f"Recherche en cours… (@{u})"),
            )
            # Pause anti-blocage Instagram
            for remaining in range(5, 0, -1):
                self.after(
                    0,
                    lambda c=count, u=username, r=remaining: self._update_search_dialog(
                        c, f"Attente anti-blocage: {r}s (profil @{u})", analyzed=c, retained=count
                    ),
                )
                time.sleep(1)
            try:
                followers = int(getattr(p, "followers", 0) or 0)
                if followers < filters.min_followers:
                    continue

                full_name = (getattr(p, "full_name", "") or "").strip()
                nom, prenom = self._split_name(full_name)
                post_dates = []
                try:
                    for i, post in enumerate(p.get_posts()):
                        if i >= 12:
                            break
                        post_dates.append(post.date_utc.replace(tzinfo=dt.timezone.utc))
                except Exception:
                    post_dates = []

                row = {c: "" for c in CSV_COLUMNS}
                row["Nom"] = nom
                row["Prénom"] = prenom
                row["Date d'ajout"] = now_str()
                row["Sport"] = terms[0] if terms else ""
                row["Club"] = terms[1] if len(terms) > 1 else ""
                row["Ville"] = ""
                row["Instagram"] = f"https://instagram.com/{p.username}"
                row["Nombre d'abonnés"] = str(followers)
                row["Nombre de posts"] = str(getattr(p, "mediacount", "") or "")
                row["Posts 3 derniers mois"] = str(count_recent_posts(post_dates))
                row["Info en bio"] = (getattr(p, "biography", "") or "").strip()
                row["Autres informations"] = "Sources: Instagram"
                row["Date de naissance"] = ""
                row["Age"] = ""
                row["Nombre de points"] = ""
                row["Niveau Barème"] = ""
                row["Nationalité"] = ""
                row["Priorité"] = ""

                age = row["Age"]
                if filters.age_min is not None and age.isdigit() and int(age) < filters.age_min:
                    continue
                if filters.age_max is not None and age.isdigit() and int(age) > filters.age_max:
                    continue

                results.append(row)
                count += 1
                self.after(
                    0,
                    lambda c=count, u=username: self._update_search_dialog(
                        c, f"Profil ajouté: @{u}", analyzed=c, retained=c
                    ),
                )
            except Exception as e:
                append_log(f"Profil ignoré ({getattr(p, 'username', '?')}): {e}")

        return results

    def _ensure_instagram_session_prompt(self) -> bool:
        """Prompt at first search to improve reliability with an IG session."""
        if self._instagram_prompt_done:
            return True
        self._instagram_prompt_done = True

        # Try default account first so first search can start without extra prompts.
        if self._ensure_default_instagram_session():
            return True

        # If a saved session exists, no need to prompt.
        if (self.instagram_session_user or "").strip():
            return True

        should_connect = messagebox.askyesno(
            APP_TITLE,
            "Aucune session Instagram détectée.\n\n"
            "Voulez-vous vous connecter maintenant ?\n"
            "(recommandé pour éviter les blocages)",
        )
        if not should_connect:
            return True

        use_browser = messagebox.askyesno(
            APP_TITLE,
            "Souhaitez-vous ouvrir Instagram dans le navigateur externe avant la connexion ?\n\n"
            "Cela peut aider (vérification compte/2FA), puis vous reviendrez dans l'application.",
        )
        if use_browser:
            webbrowser.open("https://www.instagram.com/accounts/login/")
            should_enter_credentials = messagebox.askyesno(
                APP_TITLE,
                "Si vous êtes déjà connecté dans le navigateur, vous pouvez continuer sans saisir "
                "vos identifiants dans l'application.\n\n"
                "Voulez-vous quand même saisir vos identifiants maintenant ?",
            )
            if not should_enter_credentials:
                self._set_status("Mode navigateur actif (sans session enregistrée dans l'application).")
                return True

        success = self._connect_instagram_dialog_flow()
        if not success:
            messagebox.showwarning(
                APP_TITLE,
                "Connexion Instagram non finalisée.\n"
                "La recherche va continuer, mais peut être limitée.",
            )
        return True

    def _ensure_default_instagram_session(self) -> bool:
        if not DEFAULT_IG_USERNAME or not DEFAULT_IG_PASSWORD:
            return False
        if (self.instagram_session_user or "").strip():
            return True
        try:
            insta = ensure_instaloader()
            if insta is None:
                return False
            loader = insta.Instaloader(
                download_pictures=False,
                download_video_thumbnails=False,
                download_videos=False,
                save_metadata=False,
                compress_json=False,
                max_connection_attempts=1,
            )
            loader.login(DEFAULT_IG_USERNAME, DEFAULT_IG_PASSWORD)
            session_file = self._session_file_for_user(DEFAULT_IG_USERNAME)
            loader.save_session_to_file(session_file)
            self._save_session_user(DEFAULT_IG_USERNAME)
            self._set_status(f"Session Instagram par défaut active: @{DEFAULT_IG_USERNAME}")
            return True
        except Exception as e:
            append_log(f"Compte Instagram par défaut indisponible: {e}")
            return False

    def _search_web_fallback(self, filters: SearchFilters) -> List[Dict[str, str]]:
        webdrv, webdrv_exc, by_cls = ensure_selenium()
        if webdrv is None or by_cls is None:
            raise RuntimeError(
                "Recherche limitée par Instagram. Veuillez réessayer plus tard ou utiliser un VPN."
            )

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
            raise RuntimeError(
                "Fallback web indisponible (Selenium incomplet). "
                "Relancez le build onedir puis réessayez."
            ) from e
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
            raise RuntimeError(
                "Recherche limitée par Instagram. Veuillez réessayer plus tard ou utiliser un VPN."
            ) from e
        finally:
            driver.quit()

    def _session_file_for_user(self, username: str) -> str:
        return os.path.join(DATA_DIR, f"ig_session_{username}.session")

    def _load_session_user(self) -> Optional[str]:
        try:
            if os.path.exists(SESSION_USER_FILE):
                return open(SESSION_USER_FILE, "r", encoding="utf-8").read().strip() or None
        except Exception:
            return None
        return None

    def _save_session_user(self, username: str) -> None:
        with open(SESSION_USER_FILE, "w", encoding="utf-8") as f:
            f.write(username.strip())
        self.instagram_session_user = username.strip()
        self.after(0, lambda: self._update_instagram_status(True))

    def _load_instagram_session(self, loader) -> bool:
        username = (self.instagram_session_user or "").strip()
        if not username:
            return False
        session_file = self._session_file_for_user(username)
        if not os.path.exists(session_file):
            return False
        try:
            loader.load_session_from_file(username, session_file)
            return True
        except Exception as e:
            append_log(f"Session Instagram invalide: {e}")
            return False

    def _connect_instagram_dialog_flow(self) -> bool:
        insta = ensure_instaloader()
        if insta is None:
            messagebox.showerror(APP_TITLE, "instaloader non installé.")
            return False
        credentials = self._ask_instagram_credentials()
        if not credentials:
            return False
        username, password = credentials

        self._set_status("Connexion Instagram en cours...")
        busy = BusyDialog(self, APP_TITLE, "Connexion Instagram en cours...")
        try:
            loader = insta.Instaloader(
                download_pictures=False,
                download_video_thumbnails=False,
                download_videos=False,
                save_metadata=False,
                compress_json=False,
                max_connection_attempts=1,
            )
            loader.login(username.strip(), password)
            session_file = self._session_file_for_user(username.strip())
            loader.save_session_to_file(session_file)
            self._save_session_user(username.strip())
            self._set_status(f"Session Instagram enregistrée: @{username.strip()}")
            busy.close()
            messagebox.showinfo(APP_TITLE, "Connexion Instagram réussie. Session sauvegardée.")
            return True
        except Exception as e:
            append_log(f"Connexion Instagram échouée: {e}")
            self.after(0, lambda: self._update_instagram_status(False))
            busy.close()
            messagebox.showerror(APP_TITLE, f"Connexion Instagram échouée:\n{e}")
            return False

    def _ask_instagram_credentials(self) -> Optional[tuple]:
        dlg = tk.Toplevel(self)
        dlg.title(f"{APP_TITLE} - Connexion Instagram")
        dlg.resizable(False, False)
        dlg.transient(self)
        dlg.grab_set()

        frame = ttk.Frame(dlg, padding=12)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text="Identifiant Instagram").grid(row=0, column=0, sticky="w", pady=(0, 4))
        username_var = tk.StringVar(value=(self.instagram_session_user or DEFAULT_IG_USERNAME or ""))
        username_entry = ttk.Entry(frame, textvariable=username_var, width=36)
        username_entry.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        ttk.Label(frame, text="Mot de passe Instagram").grid(row=2, column=0, sticky="w", pady=(0, 4))
        password_var = tk.StringVar(value=DEFAULT_IG_PASSWORD or "")
        password_entry = ttk.Entry(frame, textvariable=password_var, width=36, show="*")
        password_entry.grid(row=3, column=0, sticky="ew", pady=(0, 12))

        result = {"value": None}

        def on_ok() -> None:
            user = username_var.get().strip()
            pwd = password_var.get()
            if not user or not pwd:
                messagebox.showwarning(APP_TITLE, "Identifiant et mot de passe requis.", parent=dlg)
                return
            result["value"] = (user, pwd)
            dlg.destroy()

        def on_cancel() -> None:
            dlg.destroy()

        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, sticky="e")
        ttk.Button(actions, text="Annuler", command=on_cancel).pack(side="right", padx=(6, 0))
        ttk.Button(actions, text="Se connecter", command=on_ok).pack(side="right")

        frame.columnconfigure(0, weight=1)
        dlg.protocol("WM_DELETE_WINDOW", on_cancel)
        username_entry.focus_set()
        username_entry.selection_range(0, "end")
        dlg.wait_window()
        return result["value"]

    def on_connect_instagram(self) -> None:
        use_browser = messagebox.askyesno(
            APP_TITLE,
            "Ouvrir la page de connexion Instagram dans votre navigateur ?",
        )
        if use_browser:
            webbrowser.open("https://www.instagram.com/accounts/login/")
            should_enter_credentials = messagebox.askyesno(
                APP_TITLE,
                "Si vous êtes déjà connecté dans le navigateur, vous pouvez éviter la saisie "
                "des identifiants dans l'application.\n\n"
                "Voulez-vous saisir vos identifiants maintenant ?",
            )
            if not should_enter_credentials:
                self._set_status("Mode navigateur actif (sans session enregistrée dans l'application).")
                return
        self._connect_instagram_dialog_flow()

    def on_test_instagram(self) -> None:
        insta = ensure_instaloader()
        if insta is None:
            messagebox.showerror(APP_TITLE, "instaloader non installé.")
            return
        loader = insta.Instaloader(
            download_pictures=False,
            download_video_thumbnails=False,
            download_videos=False,
            save_metadata=False,
            compress_json=False,
            max_connection_attempts=1,
        )
        try:
            loaded = self._load_instagram_session(loader)
            test_search = insta.TopSearchResults(loader.context, "football")
            _ = next(iter(test_search.get_profiles()), None)
            mode = "session" if loaded else "sans session"
            self._update_instagram_status(loaded)
            messagebox.showinfo(APP_TITLE, f"Instagram OK ({mode}).")
            self._set_status(f"Test Instagram OK ({mode}).")
        except Exception as e:
            append_log(f"Test Instagram échoué: {e}")
            self._update_instagram_status(False)
            messagebox.showwarning(
                APP_TITLE,
                "Instagram indisponible actuellement.\n"
                "Utilisez Connexion Instagram ou réessayez plus tard / VPN.",
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
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            for row in self._tree_rows():
                if not row["Age"]:
                    row["Age"] = calc_age(row.get("Date de naissance", ""))
                writer.writerow(row)

    def _autosave_csv(self, query: str) -> None:
        if not self.current_csv_path:
            self.current_csv_path = generate_search_csv_path(query)
        self._write_csv(self.current_csv_path)
        self._set_status(f"Auto-sauvegarde: {self.current_csv_path}")

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
        self.current_csv_path = path
        self._set_status(f"Exporté: {path}")
        messagebox.showinfo(APP_TITLE, f"CSV exporté:\n{path}")

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
