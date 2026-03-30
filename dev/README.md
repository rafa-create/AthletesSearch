# Sportifs Manager (MVP)

Application desktop Python simple pour rechercher des profils Instagram publics de sportifs et exporter les donnees au format CSV.

## Lancer en local

1. Installer Python 3.11+.
2. Installer les dependances:

```bash
python -m pip install -r requirements.txt
```

1. Lancer:

```bash
python app.py
```

## Build portable rapide (recommande) : onedir

Double-cliquer `build_onedir.bat`.

Livrable:

- `release/SportifsManager/` (dossier portable)
- `release/Lancer_SportifsManager.bat` (double-clic pour lancer)

## Donnees et logs

Au premier lancement, l'application cree:

- `./Data/sportifs.csv`
- `./.appdata/logs/`

## Notes importantes

- Delai dynamique Instagram: tres court en mode rapide, plus prudent en mode complet.
- Si Instagram bloque, l'application affiche un message demandant de reessayer plus tard ou d'utiliser un VPN.
- Colonnes `Nombre de points` et `Niveau Bareme` laissees vides (comme demande).
