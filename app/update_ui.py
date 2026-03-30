import os
import sys
import time
import tkinter as tk
from tkinter import ttk


def main() -> int:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.abspath(os.getenv("ATHLETES_ROOT") or os.path.join(app_dir, os.pardir))
    log_path = os.path.join(root_dir, ".appdata", "logs", "update.log")

    root = tk.Tk()
    root.title("Mise à jour Athletes Searcher")
    root.geometry("760x460")
    # Keep the popup visible even if the main app closes right after clicking.
    root.attributes("-topmost", True)

    ttk.Label(
        root,
        text=(
            "Mise à jour en cours…\n\n"
            "Étapes:\n"
            "1) Téléchargement du ZIP (sans Git)\n"
            "2) Mise à jour des dépendances (pip install)\n"
            "3) Relance de l’application\n\n"
            "Cette fenêtre reste ouverte même si l’application se ferme."
        ),
        justify="left",
    ).pack(anchor="w", padx=12, pady=(10, 8))

    box = tk.Text(root, height=14, wrap="word")
    box.pack(fill="both", expand=True, padx=12, pady=(0, 8))
    box.configure(state="disabled")

    status_var = tk.StringVar(value=f"Journal: {log_path}")
    ttk.Label(root, textvariable=status_var).pack(anchor="w", padx=12, pady=(0, 10))

    btn_row = ttk.Frame(root)
    btn_row.pack(fill="x", padx=12, pady=(0, 10))
    ttk.Button(btn_row, text="Fermer", command=root.destroy).pack(side="right")

    def set_text(txt: str) -> None:
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("end", txt)
        box.see("end")
        box.configure(state="disabled")

    def poll() -> None:
        try:
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                set_text(content)
            else:
                set_text("En attente du journal de mise à jour…")
        except Exception as e:
            set_text(f"Erreur lecture journal: {e}")
        root.after(700, poll)

    poll()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

