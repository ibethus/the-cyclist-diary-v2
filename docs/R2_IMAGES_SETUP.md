# Hébergement des images sur R2 (CloudFlare)

## Vue d'ensemble

Les images WebP générées par Hugo sont hébergées sur CloudFlare R2 (stockage S3-compatible) plutôt que sur GitHub Pages. Cela permet de :
- Réduire la taille de l'artifact déployé sur GitHub Pages (~45 MB économisés)
- Bénéficier du CDN CloudFlare pour une livraison rapide des images
- Éviter de ré-uploader les images déjà présentes

## Configuration

### 1. Créer le bucket R2

Dans le dashboard CloudFlare :
1. Allez dans **R2 Object Storage**
2. Créez un bucket nommé `the-cyclist-diary-images`
3. Configurez l'accès public pour le bucket

### 2. Configurer le domaine custom

Pour utiliser `images.the-cyclist-diary.fr` :
1. Dans les paramètres du bucket R2, allez dans **Settings**
2. Section **Custom Domains**, ajoutez `images.the-cyclist-diary.fr`
3. CloudFlare créera automatiquement les enregistrements DNS nécessaires

### 3. Configurer les secrets GitHub

Ajoutez ces secrets dans les paramètres de votre repository GitHub :
- `R2_ACCESS_KEY_ID` : Votre Access Key ID R2
- `R2_ACCESS_KEY` : Votre Secret Access Key R2

Pour obtenir ces clés :
1. Dans CloudFlare, allez dans **R2** > **Manage R2 API Tokens**
2. Créez un nouveau token avec les permissions de lecture/écriture sur votre bucket

### 4. Variables d'environnement locales

Pour tester localement, créez un fichier `.env` (non versionné) :

```bash
export R2_ACCESS_KEY_ID="votre_access_key_id"
export R2_ACCESS_KEY="votre_secret_access_key"
export R2_BUCKET="the-cyclist-diary-images"
export R2_ENDPOINT="https://59d55d1a4dbc1f28fe6cd3d2d2036e4d.r2.cloudflarestorage.com"
```

Puis sourcez-le : `source .env`

## Utilisation

### Build en production

Le workflow GitHub Actions s'occupe automatiquement de :
1. Installer les dépendances Python (avec cache pour optimiser les builds)
2. Générer les images Instagram (`instagram.jpeg`, portrait 3:4) via `scripts/generate-instagram-images.py`
3. Builder le site avec Hugo
4. Synchroniser les images WebP **et les `instagram.jpeg`** vers R2 (upload parallélisé + suppression des orphelines)
5. Supprimer les images WebP/JPEG de l'artifact
6. Déployer sur GitHub Pages

**Images Instagram** : Avant le build Hugo, un script génère pour chaque article de blog et chaque étape d'aventure une image `instagram.jpeg` au format portrait 3:4, à partir de sa couverture `index.jpg`/`index.jpeg`. Le nom de l'aventure est ajouté en haut (pour les étapes) et le titre de l'article en bas, avec la charte graphique du blog. Si un bundle n'a pas de couverture, une image du dossier est copiée en `index.jpg`. Ces `instagram.jpeg` servent d'image `<enclosure>` dans le flux RSS et sont servies depuis le CDN R2. Elles sont ignorées par git (régénérées à chaque build).

**Note** : Les dépendances Python (awscli, Pillow) sont mises en cache via `actions/setup-python@v5` pour accélérer les builds successifs.
**Performance** : L'utilisation de `aws s3 sync` permet des uploads parallélisés 10-50x plus rapides qu'une copie séquentielle.

### Upload manuel des images

Si vous voulez synchroniser les images manuellement :

```bash
# 1. Builder le site en mode production
hugo --minify

# 2. Sourcer les variables d'environnement
source .env

# 3. Lancer la synchronisation
python3 scripts/upload-images-to-r2.py
```

Le script :
- Utilise `aws s3 sync` pour des uploads parallélisés ultra-rapides
- Upload uniquement les fichiers nouveaux ou modifiés
- Supprime automatiquement les images orphelines sur R2
- Configure les bonnes métadonnées (Content-Type, Cache-Control)

### Tester localement avec hugo server

En mode développement (`hugo server`), les images sont servies localement depuis `public/` sans conversion WebP. Cela accélère le développement.

En mode production, les templates Hugo génèrent des URLs pointant vers `https://images.the-cyclist-diary.fr/`.

## Architecture

### Templates modifiés

Deux templates ont été modifiés pour utiliser R2 en production :
- `layouts/shortcodes/img.html` : shortcode `{{< img src="..." >}}`
- `layouts/_default/_markup/render-image.html` : images Markdown `![alt](image.jpg)`

En mode production, ces templates :
1. Génèrent les images WebP localement via le pipeline Hugo
2. Construisent les URLs avec le CDN R2 : `{{ site.Params.imagesCDN }}{{ $image.RelPermalink }}`
3. Créent les attributs srcset pour le responsive

### Configuration Hugo

Dans `hugo.yml` :

```yaml
params:
  imagesCDN: "https://images.the-cyclist-diary.fr"
```

Cette variable est utilisée par les templates pour construire les URLs des images.

## Maintenance

### Vérifier l'espace utilisé

```bash
aws s3 ls s3://the-cyclist-diary-images/ --recursive --endpoint-url https://59d55d1a4dbc1f28fe6cd3d2d2036e4d.r2.cloudflarestorage.com --human-readable --summarize
```

### Lister les images sur R2

```bash
aws s3 ls s3://the-cyclist-diary-images/ --recursive --endpoint-url https://59d55d1a4dbc1f28fe6cd3d2d2036e4d.r2.cloudflarestorage.com
```

### Supprimer toutes les images (⚠️ attention)

```bash
aws s3 rm s3://the-cyclist-diary-images/ --recursive --endpoint-url https://59d55d1a4dbc1f28fe6cd3d2d2036e4d.r2.cloudflarestorage.com
```

## Dépannage

### Les images ne s'affichent pas

1. Vérifiez que le domaine custom est bien configuré dans R2
2. Vérifiez que les images ont bien été uploadées : `aws s3 ls ...`
3. Testez l'accès direct à une image : `https://images.the-cyclist-diary.fr/path/to/image.webp`
4. Vérifiez les en-têtes CORS si nécessaire

### Le script d'upload échoue

1. Vérifiez que AWS CLI est installé : `aws --version`
2. Vérifiez les variables d'environnement : `echo $R2_ACCESS_KEY_ID`
3. Testez la connexion : `aws s3 ls s3://the-cyclist-diary-images/ --endpoint-url ...`

### L'artifact GitHub est toujours lourd

Vérifiez que l'étape "Remove WebP images from artifact" s'est bien exécutée dans les logs du workflow.

## Performance

- **Avant** : ~45 MB d'images WebP dans l'artifact GitHub Pages
- **Après** : 0 MB d'images WebP, servies depuis le CDN CloudFlare avec cache immutable (1 an)

Le chargement des pages devrait être plus rapide grâce au CDN global de CloudFlare.
