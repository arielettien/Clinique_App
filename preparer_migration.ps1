# Prepare la migration de Clinique App vers le serveur Ubuntu.
# A executer sur le PC Windows actuel, depuis le dossier du projet :
#     .\preparer_migration.ps1
#
# Ce script fait UNE seule chose : sauvegarder la base de donnees dans un
# fichier .sql transferable. La copie des fichiers du projet (et surtout du
# dossier backend/uploads/) se fait ensuite manuellement — voir DEPLOIEMENT.md.
#
# Il ne modifie ni ne supprime rien : le PC Windows continue de fonctionner
# exactement comme avant.

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== Preparation de la migration - Clinique App ===" -ForegroundColor Cyan
Write-Host ""

# --- 1. Verifier que la base de donnees tourne ---------------------------
Write-Host "[1/4] Verification que Docker et la base de donnees sont demarres..."
$etat = docker compose ps --format "{{.Service}} {{.State}}"
if (-not $?) {
    Write-Host "ERREUR : Docker ne repond pas. Demarre Docker Desktop, attends qu'il" -ForegroundColor Red
    Write-Host "         soit completement lance, puis relance ce script." -ForegroundColor Red
    exit 1
}
$dbActive = $etat | Where-Object { $_ -match "^db\s+running" }
if (-not $dbActive) {
    Write-Host "ERREUR : le service 'db' n'est pas demarre." -ForegroundColor Red
    Write-Host "Lance d'abord :  docker compose up -d" -ForegroundColor Yellow
    exit 1
}
Write-Host "      OK - la base de donnees est accessible." -ForegroundColor Green

# --- 2. Sauvegarder la base dans le conteneur ----------------------------
# On ecrit d'abord DANS le conteneur (option -f), puis on recupere le fichier
# avec 'docker compose cp'. On evite volontairement la redirection PowerShell
# ( > fichier.sql ), qui produit un encodage que PostgreSQL ne sait pas relire.
$horodatage = Get-Date -Format "yyyy-MM-dd_HHmm"
$nomFichier = "migration_clinique_$horodatage.sql"

Write-Host "[2/4] Sauvegarde de la base de donnees en cours..."
docker compose exec -T db pg_dump -U clinic_user -d clinic_db -f /tmp/migration.sql
if (-not $?) {
    Write-Host "ERREUR : la sauvegarde a echoue." -ForegroundColor Red
    exit 1
}

Write-Host "[3/4] Recuperation du fichier de sauvegarde..."
docker compose cp db:/tmp/migration.sql ".\$nomFichier"
if (-not $?) {
    Write-Host "ERREUR : impossible de recuperer le fichier depuis le conteneur." -ForegroundColor Red
    exit 1
}
docker compose exec -T db rm -f /tmp/migration.sql | Out-Null

$taille = [math]::Round((Get-Item ".\$nomFichier").Length / 1KB, 1)
Write-Host "      OK - fichier cree : $nomFichier ($taille Ko)" -ForegroundColor Green

# --- 3. Verifier les elements indispensables a copier --------------------
Write-Host "[4/4] Verification des elements a transferer..."
Write-Host ""

$problemes = 0

if (Test-Path ".\.env") {
    Write-Host "  [OK] .env                  (mots de passe et cles - INDISPENSABLE)" -ForegroundColor Green
} else {
    Write-Host "  [!!] .env MANQUANT         - sans lui, rien ne demarrera" -ForegroundColor Red
    $problemes++
}

if (Test-Path ".\backend\uploads") {
    $nbFichiers = (Get-ChildItem ".\backend\uploads" -File -ErrorAction SilentlyContinue).Count
    $poidsMo = 0
    if ($nbFichiers -gt 0) {
        $poidsMo = [math]::Round(((Get-ChildItem ".\backend\uploads" -File | Measure-Object -Property Length -Sum).Sum / 1MB), 1)
    }
    Write-Host "  [OK] backend/uploads/      ($nbFichiers fichier(s), $poidsMo Mo - photos et signatures)" -ForegroundColor Green
} else {
    Write-Host "  [--] backend/uploads/      (absent : aucun media enregistre pour l'instant)" -ForegroundColor Yellow
}

if (Test-Path ".\backups") {
    Write-Host "  [OK] backups/              (historique chiffre - a copier aussi, conseille)" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Termine ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "A transferer sur le serveur (voir DEPLOIEMENT.md, etape 7) :" -ForegroundColor White
Write-Host "  - TOUT le dossier du projet, y compris le fichier cache .env"
Write-Host "  - le fichier $nomFichier qui vient d'etre cree"
Write-Host ""
Write-Host "A NE PAS transferer (sera regenere sur le serveur) :" -ForegroundColor White
Write-Host "  - backend/certs/   (certificat lie a l'ancienne adresse IP)"
Write-Host ""

if ($problemes -gt 0) {
    Write-Host "ATTENTION : $problemes element(s) indispensable(s) manquant(s), voir ci-dessus." -ForegroundColor Red
    exit 1
}
