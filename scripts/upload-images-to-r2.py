#!/usr/bin/env python3
"""
Script pour synchroniser les images WebP vers R2 (CloudFlare)
Utilise AWS CLI S3 sync pour des uploads parallélisés et rapides.
Supprime automatiquement les images orphelines sur R2.
"""
import os
import subprocess
import sys
from pathlib import Path

# Configuration R2
R2_BUCKET = os.environ.get('R2_BUCKET', 'the-cyclist-diary-images')
R2_ENDPOINT = os.environ.get('R2_ENDPOINT', 'https://59d55d1a4dbc1f28fe6cd3d2d2036e4d.r2.cloudflarestorage.com')
R2_ACCESS_KEY = os.environ.get('R2_ACCESS_KEY_ID')
R2_SECRET_KEY = os.environ.get('R2_ACCESS_KEY')

# Dossier source des images
PUBLIC_DIR = Path(__file__).parent.parent / "public"


def check_aws_cli():
    """Vérifie que AWS CLI est installé"""
    try:
        result = subprocess.run(['aws', '--version'], capture_output=True, check=True, text=True)
        print(f"✅ AWS CLI détecté: {result.stdout.strip()}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("❌ AWS CLI n'est pas installé. Installez-le avec:")
        print("   pip install awscli")
        print("   ou: sudo apt install awscli")
        return False


def count_webp_files() -> int:
    """Compte le nombre de fichiers WebP dans public/"""
    return len(list(PUBLIC_DIR.rglob("*.webp")))


def count_ig_jpg_files() -> int:
    """Compte le nombre de fichiers JPEG (images sources + variantes instagram.jpeg)"""
    return len(list(PUBLIC_DIR.rglob("*.jpg"))) + len(list(PUBLIC_DIR.rglob("*.jpeg")))


def sync_to_r2(delete_orphans: bool = True) -> bool:
    """
    Synchronise les images WebP vers R2 en utilisant aws s3 sync
    
    Args:
        delete_orphans: Si True, supprime les fichiers sur R2 qui n'existent plus localement
    
    Returns:
        True si succès, False sinon
    """
    print(f"\n🔄 Synchronisation vers R2 (bucket: {R2_BUCKET})...")
    print("-" * 60)
    
    cmd = [
        'aws', 's3', 'sync',
        str(PUBLIC_DIR),
        f's3://{R2_BUCKET}/',
        '--endpoint-url', R2_ENDPOINT,
        '--content-type', 'image/webp',
        '--cache-control', 'public, max-age=31536000, immutable',
        '--exclude', '*',
        '--include', '*.webp',
        '--size-only',  # Compare par taille (plus rapide que checksum)
    ]
    
    if delete_orphans:
        cmd.append('--delete')
        print("🗑️  Mode: synchronisation avec suppression des fichiers orphelins")
    else:
        print("📤 Mode: upload uniquement (pas de suppression)")
    
    print(f"⚡ Parallélisation: AWS CLI utilisera plusieurs threads automatiquement")
    print()
    
    try:
        # Exécute la commande et affiche la sortie en temps réel
        result = subprocess.run(
            cmd,
            check=True,
            text=True,
            capture_output=False  # Affiche directement dans la console
        )
        return True
    
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Erreur lors de la synchronisation: {e}")
        return False


def sync_ig_jpg_to_r2(delete_orphans: bool = True) -> bool:
    """
    Synchronise les images JPEG (sources) dédiées à Instagram vers R2.

    Le site n'utilise désormais que des images sources au format JPEG
    (l'API Graph d'Instagram exige du JPEG, incompatible avec le pipeline
    WebP habituel). Hugo publie les ressources de bundle de page telles
    quelles dans public/, sans transformation ni suffixe de hash : on
    synchronise donc directement tous les *.jpg présents.

    Args:
        delete_orphans: Si True, supprime les fichiers sur R2 qui n'existent plus localement

    Returns:
        True si succès, False sinon
    """
    print(f"\n🔄 Synchronisation des JPEG Instagram vers R2 (bucket: {R2_BUCKET})...")
    print("-" * 60)

    cmd = [
        'aws', 's3', 'sync',
        str(PUBLIC_DIR),
        f's3://{R2_BUCKET}/',
        '--endpoint-url', R2_ENDPOINT,
        '--content-type', 'image/jpeg',
        '--cache-control', 'public, max-age=31536000, immutable',
        '--exclude', '*',
        '--include', '*.jpg',
        '--include', '*.jpeg',  # variantes instagram.jpeg (portrait 3:4)
        '--size-only',  # Compare par taille (plus rapide que checksum)
    ]

    if delete_orphans:
        cmd.append('--delete')
        print("🗑️  Mode: synchronisation avec suppression des fichiers orphelins")
    else:
        print("📤 Mode: upload uniquement (pas de suppression)")

    print()

    try:
        result = subprocess.run(
            cmd,
            check=True,
            text=True,
            capture_output=False
        )
        return True

    except subprocess.CalledProcessError as e:
        print(f"\n❌ Erreur lors de la synchronisation des JPEG Instagram: {e}")
        return False


def get_r2_stats() -> dict:
    """Récupère des statistiques sur le bucket R2"""
    try:
        cmd = [
            'aws', 's3', 'ls',
            f's3://{R2_BUCKET}/',
            '--recursive',
            '--endpoint-url', R2_ENDPOINT,
            '--summarize',
            '--human-readable'
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        
        # Parse les dernières lignes qui contiennent les stats
        lines = result.stdout.strip().split('\n')
        stats = {'files': 0, 'size': '0 Bytes'}
        
        for line in lines[-5:]:  # Les stats sont dans les dernières lignes
            if 'Total Objects:' in line:
                stats['files'] = int(line.split(':')[1].strip())
            elif 'Total Size:' in line:
                stats['size'] = line.split(':')[1].strip()
        
        return stats
    
    except subprocess.CalledProcessError:
        return {'files': '?', 'size': '?'}


def main():
    """Fonction principale"""
    print("🚀 Synchronisation des images WebP vers R2")
    print("=" * 60)
    
    # Vérifications préalables
    if not check_aws_cli():
        sys.exit(1)
    
    if not R2_ACCESS_KEY or not R2_SECRET_KEY:
        print("❌ Variables d'environnement manquantes:")
        print("   R2_ACCESS_KEY_ID et R2_ACCESS_KEY doivent être définies")
        sys.exit(1)
    
    if not PUBLIC_DIR.exists():
        print(f"❌ Le dossier {PUBLIC_DIR} n'existe pas")
        print("   Lancez 'hugo' pour générer le site d'abord")
        sys.exit(1)
    
    # Configure AWS CLI avec les credentials R2
    os.environ['AWS_ACCESS_KEY_ID'] = R2_ACCESS_KEY
    os.environ['AWS_SECRET_ACCESS_KEY'] = R2_SECRET_KEY
    
    # Compte les fichiers locaux
    local_count = count_webp_files()
    print(f"📊 {local_count} fichiers WebP trouvés localement")
    ig_jpg_count = count_ig_jpg_files()
    print(f"📊 {ig_jpg_count} fichiers JPEG Instagram trouvés localement")
    
    # Synchronise vers R2 avec suppression des orphelins
    success = sync_to_r2(delete_orphans=True)
    
    if not success:
        sys.exit(1)

    # Synchronise les variantes JPEG dédiées à Instagram
    success = sync_ig_jpg_to_r2(delete_orphans=True)

    if not success:
        sys.exit(1)
    
    # Affiche les statistiques finales
    print("\n" + "=" * 60)
    print("✅ Synchronisation terminée !")
    
    stats = get_r2_stats()
    print(f"📊 Statistiques R2:")
    print(f"   • Fichiers: {stats['files']}")
    print(f"   • Taille totale: {stats['size']}")
    print(f"\n💡 Les images orphelines ont été supprimées de R2")
    print(f"💡 Les fichiers identiques n'ont pas été ré-uploadés")


if __name__ == "__main__":
    main()

