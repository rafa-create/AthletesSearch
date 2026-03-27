import csv
import datetime as dt
import os
import threading
import time
import traceback
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Optional
import webbrowser

try:
    import instaloader
except Exception:
    instaloader = None

try:
    from selenium import webdriver
    from selenium.common.exceptions import WebDriverException
    from selenium.webdriver.common.by import By
except Exception:
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
        self.win = tk.Toplevel(root)
        self.win.title("Recherche en cours…")
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", lambda: None)

        frm = ttk.Frame(self.win, padding=14)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Recherche en cours…", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        self.details = tk.StringVar(value="Initialisation…")
        ttk.Label(frm, textvariable=self.details, wraplength=520).pack(anchor="w", pady=(8, 10))

        self.bar = ttk.Progressbar(frm, mode="determinate", length=520, maximum=self.total)
        self.bar.pack(fill="x")

        self.counter = tk.StringVar(value=f"0 / {self.total}")
        ttk.Label(frm, textvariable=self.counter).pack(anchor="e", pady=(6, 0))

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

    def update(self, current: int, message: str) -> None:
        c = max(0, min(int(current), self.total))
        self.details.set(message)
        self.bar.configure(value=c)
        self.counter.set(f"{c} / {self.total}")
        self.win.update_idletasks()

    def close(self) -> None:
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
    query: str
    min_followers: int
    age_min: Optional[int]
    age_max: Optional[int]
    max_profiles: int = 20


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

    def _start_background_checks(self) -> None:
        t = threading.Thread(target=self._check_chrome_setup_non_blocking, daemon=True)
        t.start()

    def _check_chrome_setup_non_blocking(self) -> None:
        if webdriver is None:
            return
        try:
            options = webdriver.ChromeOptions()
            options.add_argument("--headless=new")
            driver = webdriver.Chrome(options=options)
            driver.quit()
        except WebDriverException:
            self.after(
                0,
                lambda: messagebox.showwarning(
                    APP_TITLE,
                    "Chrome ou ChromeDriver non détecté.\n"
                    "Si l'application doit utiliser Selenium, installez Google Chrome.",
                ),
            )

    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")

        ttk.Label(top, text="Recherche (sport + club/ville):").grid(row=0, column=0, sticky="w")
        self.query_var = tk.StringVar()
        self.query_entry = ttk.Entry(top, textvariable=self.query_var, width=45)
        self.query_entry.grid(row=0, column=1, padx=8, sticky="we")
        self.query_entry.bind("<Return>", lambda _e: self.on_search())

        ttk.Label(top, text="Abonnés min:").grid(row=0, column=2, sticky="e")
        self.min_followers_var = tk.StringVar(value="5000")
        ttk.Entry(top, textvariable=self.min_followers_var, width=10).grid(row=0, column=3, padx=6)

        ttk.Label(top, text="Age min:").grid(row=0, column=4, sticky="e")
        self.age_min_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.age_min_var, width=7).grid(row=0, column=5, padx=6)

        ttk.Label(top, text="Age max:").grid(row=0, column=6, sticky="e")
        self.age_max_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.age_max_var, width=7).grid(row=0, column=7, padx=6)

        self.search_btn = ttk.Button(top, text="Rechercher", command=self.on_search)
        self.search_btn.grid(row=0, column=8, padx=10)

        ttk.Button(top, text="Connexion Instagram", command=self.on_connect_instagram).grid(row=0, column=9, padx=4)
        ttk.Button(top, text="Tester Instagram", command=self.on_test_instagram).grid(row=0, column=10, padx=4)
        ttk.Button(top, text="Charger CSV", command=self.on_load_csv).grid(row=0, column=11, padx=4)
        ttk.Button(top, text="Exporter en CSV", command=self.on_export_csv).grid(row=0, column=12, padx=4)
        ttk.Button(top, text="Ajouter manuellement", command=self.on_add_manual).grid(row=0, column=13, padx=4)
        ttk.Button(top, text="Aide", command=self.on_help).grid(row=0, column=14, padx=4)

        top.columnconfigure(1, weight=1)

        status_frame = ttk.Frame(self, padding=(10, 0, 10, 5))
        status_frame.pack(fill="x")
        self.status_var = tk.StringVar(value="Prêt.")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(status_frame, orient="horizontal", mode="determinate", length=280)
        self.progress.pack(side="right")

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

    def _get_filters(self) -> Optional[SearchFilters]:
        query = self.query_var.get().strip()
        if not query:
            messagebox.showwarning(APP_TITLE, "Veuillez renseigner un texte de recherche.")
            return None
        try:
            min_followers = int(self.min_followers_var.get().strip() or "0")
            age_min = int(self.age_min_var.get().strip()) if self.age_min_var.get().strip() else None
            age_max = int(self.age_max_var.get().strip()) if self.age_max_var.get().strip() else None
        except ValueError:
            messagebox.showerror(APP_TITLE, "Filtres invalides. Utilisez des nombres.")
            return None
        return SearchFilters(query=query, min_followers=min_followers, age_min=age_min, age_max=age_max)

    def on_search(self) -> None:
        filters = self._get_filters()
        if not filters:
            return
        if not self._ensure_instagram_session_prompt():
            return
        self.search_btn.configure(state="disabled")
        self.progress.configure(value=0, maximum=filters.max_profiles)
        self._set_status("Recherche en cours...")
        if self._search_dialog is not None:
            self._search_dialog.close()
        self._search_dialog = SearchProgressDialog(self, total=filters.max_profiles)
        self._search_dialog.update(0, "Connexion à Instagram…")
        t = threading.Thread(target=self._run_search, args=(filters,), daemon=True)
        t.start()

    def _run_search(self, filters: SearchFilters) -> None:
        try:
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

    def _close_search_dialog(self) -> None:
        if self._search_dialog is not None:
            self._search_dialog.close()
            self._search_dialog = None

    def _update_search_dialog(self, current: int, message: str) -> None:
        if self._search_dialog is not None:
            self._search_dialog.update(current, message)

    def _search_instagram(self, filters: SearchFilters) -> List[Dict[str, str]]:
        if instaloader is None:
            raise RuntimeError("instaloader non installé. Installez les dépendances d'abord.")

        loader = instaloader.Instaloader(
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
            search = instaloader.TopSearchResults(loader.context, search_term)
            profiles = list(search.get_profiles())
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
                        c, f"Attente anti-blocage: {r}s (profil @{u})"
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
                self.after(0, lambda c=count: self.progress.configure(value=c))
                self.after(
                    0,
                    lambda c=count, u=username: self._update_search_dialog(
                        c, f"Profil ajouté: @{u} (total {c})"
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

    def _search_web_fallback(self, filters: SearchFilters) -> List[Dict[str, str]]:
        if webdriver is None or By is None:
            raise RuntimeError(
                "Recherche limitée par Instagram. Veuillez réessayer plus tard ou utiliser un VPN."
            )

        query = filters.query.replace(",", " ").strip()
        terms = [x for x in query.split() if x]
        if not terms:
            return []

        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1600,1000")
        driver = webdriver.Chrome(options=options)
        results: List[Dict[str, str]] = []
        seen = set()

        try:
            search_query = f"site:instagram.com {query} athlete"
            url = f"https://duckduckgo.com/?q={search_query}"
            driver.get(url)
            time.sleep(2)

            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='instagram.com/']")
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
                self.after(0, lambda c=current: self.progress.configure(value=c))
                self.after(
                    0,
                    lambda c=current, u=username: self._update_search_dialog(
                        c, f"Fallback web: profil @{u} détecté (total {c})"
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
        if instaloader is None:
            messagebox.showerror(APP_TITLE, "instaloader non installé.")
            return False
        username = simpledialog.askstring(
            APP_TITLE,
            "Nom d'utilisateur Instagram:",
            initialvalue=self.instagram_session_user or "",
            parent=self,
        )
        if not username:
            return False
        password = simpledialog.askstring(APP_TITLE, "Mot de passe Instagram:", show="*", parent=self)
        if not password:
            return False

        self._set_status("Connexion Instagram en cours...")
        try:
            loader = instaloader.Instaloader(
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
            messagebox.showinfo(APP_TITLE, "Connexion Instagram réussie. Session sauvegardée.")
            return True
        except Exception as e:
            append_log(f"Connexion Instagram échouée: {e}")
            messagebox.showerror(APP_TITLE, f"Connexion Instagram échouée:\n{e}")
            return False

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
        if instaloader is None:
            messagebox.showerror(APP_TITLE, "instaloader non installé.")
            return
        loader = instaloader.Instaloader(
            download_pictures=False,
            download_video_thumbnails=False,
            download_videos=False,
            save_metadata=False,
            compress_json=False,
            max_connection_attempts=1,
        )
        try:
            loaded = self._load_instagram_session(loader)
            test_search = instaloader.TopSearchResults(loader.context, "football")
            _ = next(iter(test_search.get_profiles()), None)
            mode = "session" if loaded else "sans session"
            messagebox.showinfo(APP_TITLE, f"Instagram OK ({mode}).")
            self._set_status(f"Test Instagram OK ({mode}).")
        except Exception as e:
            append_log(f"Test Instagram échoué: {e}")
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
        path = filedialog.asksaveasfilename(
            title="Exporter en CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile="sportifs.csv",
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
            self._autosave_csv(self.query_var.get().strip() or "manuel")
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
    if webdriver is None:
        return
    try:
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        driver = webdriver.Chrome(options=options)
        driver.quit()
    except WebDriverException:
        root = tk.Tk()
        root.withdraw()
        messagebox.showwarning(
            APP_TITLE,
            "Chrome ou ChromeDriver non détecté.\n"
            "Installez Google Chrome pour activer la recherche Selenium si nécessaire.",
        )
        root.destroy()


def main() -> None:
    ensure_app_folders()
    app = AthleteApp()
    app.mainloop()


if __name__ == "__main__":
    main()
