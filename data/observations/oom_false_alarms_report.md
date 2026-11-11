# Fausses alertes du prédicteur mémoire en conditions réelles

_Généré le 2026-10-31 09:37 depuis `data/observations/oom_false_alarms.jsonl`._

- Durée d'observation : **8.0 h**, dont **8.0 h valides** (API joignable, metrics-server fonctionnel). Relevé toutes les 30 s.
- Pods surveillables (avec limite mémoire) : **18** en médiane.
- Version du prédicteur : **v1**.
- Alerte = prédiction exposée par l'application (confiance ≥ 0,25), comme dans la cloche.
- Vraie alerte : OOMKilled du même pod entre le début de l'alerte et 5 min après sa fin.

## Synthèse

| | Nombre |
|---|---|
| Épisodes d'alerte | 46 |
| dont vraies alertes | 11 |
| dont **fausses alertes** | **35** |
| Alertes non conclues à la fin de l'observation (exclues) | 2 |
| Coupures d'observation (script, API ou mesures arrêtés) | 1, 2 min au total |
| Crashs mémoire réels (OOMKilled) | 11 |
| dont survenus pendant une coupure (non observés) | 0 |
| dont annoncés | 11 |
| dont **manqués** | **0** |
| Fausses alertes par heure valide | 4.36 |
| Fausses alertes par pod surveillable et par jour | 5.81 |

## Par famille de pods

| Famille | Alertes | Vraies | Fausses | OOM réels | OOM manqués | Préavis médian | Erreur médiane d'échéance |
|---|---|---|---|---|---|---|---|
| charge normale (aikube-fa) | 35 | 0 | 35 | 0 | 0 | — | — |
| témoin qui fuit (leak-control) | 11 | 11 | 0 | 11 | 0 | 38.3 min | 0.5 min |

## Les fausses alertes, en détail

- Survenues sur un conteneur démarré depuis **moins de 10 min** : **2 sur 35** (montée de mémoire au démarrage).
- Durée médiane d'une fausse alerte : 2.0 min.
- Confiance au déclenchement (médiane) : 0.54 ; points utilisés (médiane) : 3.

| Pod | Début | Durée | Âge du conteneur | Échéance annoncée | Confiance | Points |
|---|---|---|---|---|---|---|
| `aikube-fa/cache-warm-6ddb7545b7-nkldw` | 29/10 19:24 | 68.0 min | 3 min | 17.2 min | 0.6 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 19:31 | 1.0 min | 10 min | 17.8 min | 0.256 | 8 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 19:57 | 3.0 min | 36 min | 5.8 min | 0.42 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:03 | 1.0 min | 42 min | 9.2 min | 0.514 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:10 | 3.0 min | 49 min | 8.8 min | 0.47 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:18 | 1.0 min | 57 min | 3.2 min | 0.552 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:23 | 1.0 min | 62 min | 2.5 min | 0.779 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:26 | 3.0 min | 65 min | 5.1 min | 0.575 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:31 | 3.0 min | 70 min | 2.8 min | 0.55 | 3 |
| `aikube-fa/cache-warm-6ddb7545b7-nkldw` | 29/10 20:34 | 68.0 min | 73 min | 17.2 min | 0.6 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:49 | 2.0 min | 88 min | 12.5 min | 0.294 | 5 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 20:53 | 1.0 min | 92 min | 3.5 min | 0.536 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 21:09 | 1.0 min | 108 min | 14.7 min | 0.274 | 8 |
| `aikube-fa/cache-warm-6ddb7545b7-nkldw` | 29/10 21:44 | 68.0 min | 143 min | 17.2 min | 0.6 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 22:05 | 1.0 min | 164 min | 12.2 min | 0.56 | 8 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 22:24 | 1.0 min | 183 min | 6.0 min | 0.373 | 5 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 22:32 | 1.0 min | 191 min | 6.7 min | 0.464 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 22:39 | 1.0 min | 198 min | 2.2 min | 0.902 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 22:42 | 1.0 min | 201 min | 5.0 min | 0.993 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 22:53 | 2.0 min | 212 min | 4.2 min | 0.564 | 3 |
| `aikube-fa/cache-warm-6ddb7545b7-nkldw` | 29/10 22:54 | 68.0 min | 213 min | 17.2 min | 0.6 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 23:33 | 1.0 min | 252 min | 2.8 min | 0.422 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 29/10 23:47 | 1.0 min | 266 min | 4.6 min | 0.478 | 3 |
| `aikube-fa/cache-warm-6ddb7545b7-nkldw` | 30/10 00:04 | 68.0 min | 283 min | 17.3 min | 0.6 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 00:17 | 1.0 min | 296 min | 13.6 min | 0.371 | 8 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 00:23 | 5.0 min | 302 min | 14.2 min | 0.537 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 00:48 | 5.0 min | 327 min | 13.5 min | 0.292 | 7 |
| `aikube-fa/cache-warm-6ddb7545b7-nkldw` | 30/10 01:14 | 68.0 min | 353 min | 17.2 min | 0.6 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 01:21 | 6.0 min | 360 min | 5.5 min | 0.595 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 01:42 | 1.0 min | 381 min | 4.2 min | 0.417 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 01:59 | 2.0 min | 398 min | 14.8 min | 0.255 | 8 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 02:30 | 2.0 min | 429 min | 8.1 min | 0.251 | 4 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 02:36 | 5.0 min | 435 min | 14.2 min | 0.48 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 02:55 | 3.0 min | 454 min | 2.4 min | 0.88 | 3 |
| `aikube-fa/gc-noisy-5857964cbd-7fws4` | 30/10 03:03 | 3.0 min | 462 min | 7.1 min | 0.532 | 3 |

## Lecture

- Les pods de `aikube-fa` marqués `no-oom` ne doivent jamais crasher : toute alerte sur eux est une fausse alerte par construction.
- `leak-control` fuit à 3 Mio/min et crashe environ toutes les 40 min : il vérifie, dans les mêmes conditions, que les vraies fuites restent annoncées.
- Les pods système (`kube-system`) sont de vrais composants, non modifiés.
