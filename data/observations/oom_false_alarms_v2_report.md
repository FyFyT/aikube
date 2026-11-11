# Fausses alertes du prédicteur mémoire en conditions réelles

_Généré le 2026-10-31 09:37 depuis `data/observations/oom_false_alarms_v2.jsonl`._

- Durée d'observation : **11.7 h**, dont **9.7 h valides** (API joignable, metrics-server fonctionnel). Relevé toutes les 30 s.
- Pods surveillables (avec limite mémoire) : **19** en médiane.
- Version du prédicteur : **v2**.
- Alerte = prédiction exposée par l'application (confiance ≥ 0,25), comme dans la cloche.
- Vraie alerte : OOMKilled du même pod entre le début de l'alerte et 5 min après sa fin.

## Synthèse

| | Nombre |
|---|---|
| Épisodes d'alerte | 26 |
| dont vraies alertes | 14 |
| dont **fausses alertes** | **12** |
| Alertes non conclues à la fin de l'observation (exclues) | 1 |
| Coupures d'observation (script, API ou mesures arrêtés) | 1, 120 min au total |
| Crashs mémoire réels (OOMKilled) | 15 |
| dont survenus pendant une coupure (non observés) | 1 |
| dont annoncés | 14 |
| dont **manqués** | **0** |
| Fausses alertes par heure valide | 1.24 |
| Fausses alertes par pod surveillable et par jour | 1.56 |

## Par famille de pods

| Famille | Alertes | Vraies | Fausses | OOM réels | OOM manqués | Préavis médian | Erreur médiane d'échéance |
|---|---|---|---|---|---|---|---|
| charge normale (aikube-fa) | 12 | 0 | 12 | 0 | 0 | — | — |
| témoin qui fuit (leak-control) | 14 | 14 | 0 | 15 | 0 | 36.0 min | 0.5 min |

## Les fausses alertes, en détail

- Survenues sur un conteneur démarré depuis **moins de 10 min** : **1 sur 12** (montée de mémoire au démarrage).
- Durée médiane d'une fausse alerte : 9.0 min.
- Confiance au déclenchement (médiane) : 0.88 ; points utilisés (médiane) : 5.

| Pod | Début | Durée | Âge du conteneur | Échéance annoncée | Confiance | Points |
|---|---|---|---|---|---|---|
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 30/10 21:46 | 9.0 min | 5 min | 14.7 min | 0.593 | 5 |
| `aikube-fa/gc-noisy-5857964cbd-jbh7s` | 30/10 22:39 | 1.0 min | 58 min | 7.5 min | 0.475 | 5 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 30/10 22:56 | 9.0 min | 75 min | 14.5 min | 0.586 | 5 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 31/10 01:21 | 4.0 min | 220 min | 9.6 min | 0.983 | 8 |
| `aikube-fa/gc-noisy-5857964cbd-jbh7s` | 31/10 02:16 | 1.0 min | 275 min | 12.0 min | 0.778 | 8 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 31/10 02:26 | 9.0 min | 285 min | 14.5 min | 0.976 | 5 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 31/10 03:36 | 9.0 min | 355 min | 14.5 min | 0.976 | 5 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 31/10 04:46 | 9.0 min | 425 min | 14.5 min | 0.976 | 5 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 31/10 05:56 | 9.0 min | 495 min | 14.5 min | 0.976 | 5 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 31/10 07:06 | 9.0 min | 565 min | 14.5 min | 0.976 | 5 |
| `aikube-fa/cache-warm-6ddb7545b7-7q8sq` | 31/10 08:16 | 9.5 min | 635 min | 13.8 min | 0.599 | 5 |
| `aikube-fa/gc-noisy-5857964cbd-jbh7s` | 31/10 08:26 | 1.0 min | 645 min | 10.5 min | 0.722 | 5 |

## Lecture

- Les pods de `aikube-fa` marqués `no-oom` ne doivent jamais crasher : toute alerte sur eux est une fausse alerte par construction.
- `leak-control` fuit à 3 Mio/min et crashe environ toutes les 40 min : il vérifie, dans les mêmes conditions, que les vraies fuites restent annoncées.
- Les pods système (`kube-system`) sont de vrais composants, non modifiés.
