# Build macOS (M1 / arm64) depuis Windows

Tu as un poste **Windows** et tu veux livrer une app **macOS M1** avec mise à jour.

## Principe

- La build `.app` ne peut pas être faite sur Windows.
- On la fait sur **GitHub Actions** (runner macOS).
- Le workflow produit un asset `AthletesSearcher-macOS-arm64.zip` dans une **GitHub Release**.
- Le bouton **Mise à jour** sur macOS télécharge la **dernière Release** et remplace l’app.

## 1) Publier une version

1. Mets à jour `APP_VERSION` dans `app/app.py` (ex: `v01.00.01`)
2. Commit + push sur GitHub
3. Crée un tag et pousse-le :

```bash
git tag v01.00.01
git push origin v01.00.01
```

Le workflow `.github/workflows/build-macos-arm64.yml` tourne et crée automatiquement une Release avec:
- `AthletesSearcher-macOS-arm64.zip`

## 2) Envoyer au client

Télécharge le zip de la Release, puis:
- soit tu l’envoies directement,
- soit tu utilises `app/Creer_Livraison_Client_macOS_M1.bat` pour préparer un dossier client.

## Notes

- Build faite sur `macos-14` (Apple Silicon).
- L’updater macOS est embarqué et exécuté depuis un dossier temporaire.

