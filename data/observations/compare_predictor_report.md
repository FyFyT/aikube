# Prédicteur mémoire : v1 contre v2 — 2026-10-31 09:37

## Charge normale de 8 h (fausses alertes et vraies fuites)

| | v1 (29/10) | v2 |
|---|---|---|
| Heures valides | 8.0 | 9.7 |
| Crashs réels (OOMKilled) | 11 | 15 |
| dont survenus pendant une coupure (non observés) | 0 | 1 |
| dont annoncés | 11 | 14 |
| dont manqués | 0 | 0 |
| **Fausses alertes** | **35** | **12** |
| Fausses alertes par heure | 4.36 | 1.24 |
| Temps total d'affichage des fausses alertes | 470 min | 79 min |
| Part des alertes qui étaient vraies | 24 % | 54 % |
| Préavis médian (vraies fuites) | 38.3 min | 36.0 min |
| Erreur médiane d'échéance | 0.5 min | 0.5 min |

Fausses alertes par pod : cache-warm 6 → 9 · gc-noisy 29 → 3

## Campagne du chapitre 8 — prédicteur v1 (chapitre 8)

Limite mémoire 192 Mio, relevé toutes les 15 s. Prédiction « exposée » = confiance ≥ 0.25. Préavis = instant de l'OOM réel − instant de la 1re alerte. Erreur d'échéance = instant prédit − instant réel (positif = prédiction trop tardive).

| Fuite (Mio/min) | Manches | OOM observés | Préavis prédicteur (méd.) | Préavis seuil 75 % (méd.) | Erreur à la 1re alerte (méd.) | Erreur absolue, tous relevés (méd.) | Biais (méd.) |
|---|---|---|---|---|---|---|---|
| 24 | 3 | 3 | 4.6 min | 1.0 min | 0.3 min | 0.3 min | 0.3 min |
| 12 | 3 | 3 | 12.5 min | 3.1 min | 0.4 min | 0.3 min | 0.3 min |
| 6 | 3 | 3 | 27.5 min | 7.3 min | 1.4 min | 0.3 min | 0.3 min |

Pod témoin sans fuite : 0/3 manche(s) avec une fausse alerte (durée observée médiane 32 min).

## Campagne du chapitre 8 — prédicteur v2

Limite mémoire 192 Mio, relevé toutes les 15 s. Prédiction « exposée » = confiance ≥ 0.25. Préavis = instant de l'OOM réel − instant de la 1re alerte. Erreur d'échéance = instant prédit − instant réel (positif = prédiction trop tardive).

| Fuite (Mio/min) | Manches | OOM observés | Préavis prédicteur (méd.) | Préavis seuil 75 % (méd.) | Erreur à la 1re alerte (méd.) | Erreur absolue, tous relevés (méd.) | Biais (méd.) |
|---|---|---|---|---|---|---|---|
| 24 | 3 | 3 | 3.1 min | 1.1 min | 0.2 min | 0.2 min | 0.2 min |
| 12 | 3 | 3 | 10.9 min | 3.1 min | 0.0 min | 0.2 min | 0.2 min |
| 6 | 3 | 3 | 26.6 min | 7.6 min | 0.3 min | 0.2 min | 0.0 min |

Pod témoin sans fuite : 0/3 manche(s) avec une fausse alerte (durée observée médiane 31 min).

