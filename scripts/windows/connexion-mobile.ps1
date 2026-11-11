# =============================================================================
# AI-Kube : connexion de l'application mobile (a lancer sous Windows)
# =============================================================================
# Le backend et Expo tournent dans WSL, dont l'adresse change a chaque
# redemarrage du PC. Le telephone, lui, ne voit que l'adresse Windows.
# Ce script :
#   1. trouve la distribution Ubuntu et son adresse WSL ;
#   2. trouve l'adresse Windows sur le Wi-Fi (ou le partage de connexion) ;
#   3. redirige les ports 8000 (backend) et 8081 (Expo) de Windows vers WSL ;
#   4. ouvre ces deux ports dans le pare-feu ;
#   5. verifie que le backend repond, et ecrit l'adresse pour le script WSL.
# Lancement : double-clic sur connexion-mobile.bat (demande les droits admin).
# =============================================================================
$ErrorActionPreference = 'Stop'
$ports = 8000, 8081

function Ok($m)   { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [X]  $m" -ForegroundColor Red; Read-Host "`nEntree pour fermer"; exit 1 }

Write-Host "`nAI-Kube : connexion de l'application mobile`n" -ForegroundColor Cyan

# 1. Distribution Ubuntu (et non docker-desktop) et son adresse
$distro = (wsl -l -q) -replace "`0", '' | Where-Object { $_ -match '^Ubuntu' } | Select-Object -First 1
if (-not $distro) { Fail "Aucune distribution Ubuntu trouvee (wsl -l -q)." }
$distro = $distro.Trim()
$wslIp = ((wsl -d $distro -- hostname -I) -split '\s+' | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' } | Select-Object -First 1)
if (-not $wslIp) { Fail "Adresse WSL introuvable. Ouvre un terminal Ubuntu puis relance." }
Ok "Ubuntu ($distro) : $wslIp"

# 2. Adresse Windows sur le reseau qui a une passerelle (Wi-Fi ou partage de connexion)
$cfg = Get-NetIPConfiguration | Where-Object {
    $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.InterfaceAlias -notmatch 'vEthernet|WSL|Loopback'
} | Select-Object -First 1
if (-not $cfg) { Fail "Aucun reseau connecte. Branche le Wi-Fi ou le partage de connexion du telephone." }
$winIp = ($cfg.IPv4Address | Select-Object -First 1).IPAddress
Ok "Windows ($($cfg.InterfaceAlias)) : $winIp"

# 3. Redirection des ports
foreach ($p in $ports) {
    netsh interface portproxy delete v4tov4 listenport=$p listenaddress=0.0.0.0 2>$null | Out-Null
    netsh interface portproxy add v4tov4 listenport=$p listenaddress=0.0.0.0 connectport=$p connectaddress=$wslIp | Out-Null
}
Ok "Ports 8000 et 8081 rediriges vers $wslIp"

# 4. Pare-feu (tous profils : la salle de soutenance peut etre un reseau "public")
if (-not (Get-NetFirewallRule -DisplayName 'AI-Kube mobile' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'AI-Kube mobile' -Direction Inbound -Protocol TCP -LocalPort $ports -Action Allow -Profile Any | Out-Null
}
Ok "Pare-feu ouvert pour 8000 et 8081"

# 5. Le backend repond-il ?
try {
    $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://${winIp}:8000/health"
    Ok "Backend joignable sur http://${winIp}:8000"
} catch {
    Warn "Le backend ne repond pas sur http://${winIp}:8000/health."
    Warn "Lance-le dans Ubuntu (uvicorn main:app --host 0.0.0.0 --port 8000), puis relance ce script."
}

Set-Content -Path "$env:USERPROFILE\aikube-mobile-ip.txt" -Value $winIp -Encoding ascii
Write-Host "`nA faire maintenant :" -ForegroundColor Cyan
Write-Host "  1. Dans Ubuntu :            bash ~/aikube/scripts/mobile-demarrer.sh"
Write-Host "  2. Dans l'app, Reglages :   http://${winIp}:8000   (si l'adresse a change)"
Write-Host "  3. Le telephone doit etre sur le MEME reseau que le PC.`n"
Read-Host "Entree pour fermer"
