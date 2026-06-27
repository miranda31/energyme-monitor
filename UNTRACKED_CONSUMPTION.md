# Modèle de calcul de la consommation non suivie

## Contexte matériel

Le dispositif EnergyMe repose sur un ESP32 couplé à un ADE7953 (IC de mesure de puissance,
2 canaux courant + 1 tension partagée). 16 canaux sont obtenus via un multiplexeur analogique.

**Fréquences de polling :**
- CH0 (réseau/grid) : mesuré en continu, rafraîchi toutes les ~200 ms
- CH1–CH15 : rotation round-robin stricte, un canal toutes les ~6,2 s

---

## Problème : pourquoi la puissance instantanée (W) est trompeuse

### Les deux types de données de l'ADE7953

| Champ API | Nature | Mise à jour |
|---|---|---|
| `activePower` | Puissance instantanée (W) | Dernière fenêtre du canal sélectionné par le MUX |
| `activeEnergyImported` | Énergie cumulée importée (Wh) | Accumulateur matériel continu, indépendant du MUX |
| `activeEnergyExported` | Énergie cumulée exportée (Wh) | Accumulateur matériel continu, indépendant du MUX |

Les accumulateurs Wh tournent en permanence dans le silicium, quel que soit le canal sélectionné
par le multiplexeur. La puissance instantanée, elle, n'est valide que pour le canal actuellement
sous tension du MUX — et peut être périmée de 6,2 s pour les canaux CH1–CH15.

### Scénario concret : le décalage temporel

```
t = 0 s    MUX sélectionne CH11 (frigo/chauffe-eau)
           CH11.activePower = 88,9 W  (compresseur en marche)
           CH0.activePower  = 163 W   (cohérent : CH11 + autres actifs)

t = 3 s    Le compresseur s'arrête
           CH0 reflète le changement immédiatement : passe à ~106 W

t = 6,2 s  Le collecteur Python interroge l'API
           CH0.activePower  → 106 W   (valeur fraîche, 200 ms)
           CH11.activePower → 88,9 W  (PÉRIMÉE : lue à t=0, avant arrêt)

Calcul naïf :
  non_suivi = CH0 − Σ(CH1..CH15)
            = 106 − (88,9 + autres...)  → négatif ou très élevé selon le cycle
```

Ce décalage est structurel et inévitable avec un MUX : CH0 est toujours frais,
les autres canaux ont jusqu'à 6,2 s de retard. Toute charge cyclique (compresseur de frigo,
pompe, machine à laver) génère des écarts importants à l'instant de lecture.

### Pourquoi Home Assistant est correct et le dashboard natif EnergyMe est faux

- **Home Assistant** utilise les compteurs Wh via MQTT → immunisé au décalage temporel
- **Dashboard natif EnergyMe** utilise `activePower` → exposé au même biais
- **energyme-monitor (avant implémentation Wh)** : même biais que le dashboard natif

---

## Solution : calcul par deltas d'énergie (Wh)

### Bilan énergétique correct

Sur une fenêtre de temps Δt :

```
énergie_réseau_nette + énergie_production_nette = énergie_charges_nette + non_suivi
```

Avec la convention de signe :
- `net_ch = e_in − e_out` (positif = consommateur net, négatif = producteur net)

D'où :

```
non_suivi_Wh = net_CH0 − Σ(net_CHi)   pour i = 1..15
```

Et en puissance moyenne équivalente :

```
non_suivi_W = non_suivi_Wh / (fenêtre_min / 60)
```

### Pourquoi cette formule est universelle

Elle couvre tous les rôles de canaux sans traitement spécial :

| Rôle | net_ch = e_in − e_out | Effet dans la somme |
|---|---|---|
| `load` (charge) | > 0 (consomme) | Réduit le non-suivi |
| `pv` / `inverter` | < 0 (produit net) | Augmente le non-suivi (énergie produite qui a alimenté des charges non mesurées) |
| `battery` | variable | Correctement intégré |
| `grid` (CH0) | terme de référence | Numérateur du bilan |

**Exemple avec CH2 (circuit extérieur : solaire + batterie + pompe + frigo) :**

CH2 est net exportateur (`e_out > e_in`), donc `net_CH2 < 0`.

```
non_suivi = net_CH0 − (net_CH2 + Σ_autres_charges)
          = net_CH0 − net_CH2_négatif − Σ_autres_charges
          = net_CH0 + |production_nette_CH2| − Σ_autres_charges
```

La production de CH2 s'additionne correctement à l'énergie disponible, comme pour CH0.
Les charges embarquées sur le même circuit (pompe, frigo) sont intégrées dans la mesure nette
de CH2 et n'apparaissent donc pas dans le "non suivi".

### Immunité au décalage temporel

Les accumulateurs Wh intègrent en continu dans le silicium. Sur une fenêtre de 15 minutes :
- CH11 comptabilise chaque watt-heure du compresseur dès qu'il passe dans la fenêtre du MUX
- CH0 fait de même en continu
- Le delta final est exact indépendamment de l'instant de lecture de l'API

---

## Implémentation dans energyme-monitor

### Calcul dans `metrics.jinja2`

```jinja2
{% set tw_h = (trend_minutes | default(15)) / 60.0 %}
{% set ns_ut = namespace(g_net=0.0, other_net=0.0, valid=false) %}

{% for ch in channels %}
  {% set tr_ch = trends.get(ch.index) if trends else none %}
  {% if tr_ch and ch.metrics %}
    {% if ch.role == 'grid' %}
      {# CH0 : référence du bilan #}
      {% set ns_ut.g_net = (tr_ch.e_in_delta or 0) - (tr_ch.e_out_delta or 0) %}
      {% set ns_ut.valid = true %}
    {% elif tr_ch.e_in_delta is not none or tr_ch.e_out_delta is not none %}
      {# Tous les autres canaux avec des données Wh #}
      {% set ns_ut.other_net = ns_ut.other_net
           + (tr_ch.e_in_delta or 0) - (tr_ch.e_out_delta or 0) %}
    {% endif %}
  {% endif %}
{% endfor %}

{% set ut_wh = ns_ut.g_net - ns_ut.other_net %}
{% set ut_w  = (ut_wh / tw_h) | round(0) | int %}
```

### Source des données Wh

`tr_ch.e_in_delta` et `tr_ch.e_out_delta` sont calculés par `timeseries.py` /
`_compute_trend()` : différence entre la dernière et la première valeur de
`activeEnergyImported` / `activeEnergyExported` sur la fenêtre de tendance (15 min par défaut).

```python
# Dans _compute_trend()
if len(ein_vals) >= 2:
    base["e_in_delta"] = round(ein_vals[-1] - ein_vals[0], 3)
if len(eout_vals) >= 2:
    base["e_out_delta"] = round(eout_vals[-1] - eout_vals[0], 3)
```

### Affichage

La carte "Non suivi" affiche la puissance moyenne équivalente (W) avec code couleur :
- < 100 W : vert (`success`)
- 100–499 W : orange (`warning`)
- ≥ 500 W : rouge (`danger`)

---

## Observations terrain (firmware 2.0.3, installation réelle)

### Multiplexeur

Firmware 2.0.3 : rotation round-robin stricte, ~6,2 s par canal.
Le bug WDRR load-discard de l'issue #149 (firmware 2.0.1) est corrigé.
Pas de canal ignoré, pas de pondération asymétrique.

### CH2 "extérieur et solaire"

Circuit mixte : 4 panneaux solaires (dont 2 avec batterie ~700 Wh), pompe immergée, frigo, etc.
- Net exportateur sur l'historique (`e_out = 59 636 Wh > e_in = 42 675 Wh`)
- Puissance réactive positive la nuit (~+28 VAR) : signature normale des alimentations
  à découpage de l'onduleur en veille — pas une inversion CT
- Les charges du circuit (pompe, frigo) sont mesurées dans le net de CH2, pas dans le non-suivi

### CH6 "éclairage entrée sdb"

Lectures invalides persistantes (courant 0,009 A, PF > 1) → zéros forcés par le firmware.
Cause probable : mauvaise connexion du TC. Nécessite recalibration.

### File MQTT

Queue de 132–133 entrées, 85 envois par cycle (limite 5 Ko AWS IoT).
~65 entrées toujours en attente. Home Assistant reçoit toutes les données mais avec un délai.
N'affecte pas la précision des Wh (les valeurs cumulées restent cohérentes).
