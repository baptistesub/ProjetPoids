"""Le Portionneur — calculs purs et interface Streamlit centrée sur les portions."""
import copy
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo
from uuid import uuid4
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import streamlit as st

NUTRIENTS = {"prot": "Protéines", "gluc": "Glucides", "lip": "Lipides", "fibres": "Fibres"}
DAY_DEFAULTS = {"kcal": 2400.0, "prot": 120.0, "gluc": 265.0, "lip": 95.0, "fibres": 30.0}
MEAL_WEIGHTS = {"Petit déjeuner": 25, "Déjeuner": 35, "Dîner": 30, "Collation": 10}
DAY_CONFIG_KEY = "_portionneur_journee_v1"


def automatic_targets(kcal, protein_percent=20, fat_percent=35, fibres=30, *, body_weight=None, protein_per_kg=2):
    energy = number(kcal, "Calories", positive=True)
    protein = number(protein_percent, "Part des protéines")
    fat = number(fat_percent, "Part des lipides")
    if body_weight is not None:
        grams = number(body_weight, "Poids actuel", positive=True) * number(protein_per_kg, "Protéines par kg", positive=True)
        protein = grams * 400 / energy
    if protein + fat >= 100:
        raise ValueError("Les protéines et les lipides occupent toutes les calories : augmentez les calories ou ajustez ces réglages pour garder une part de glucides.")
    return {"kcal": float(energy), "prot": float(energy * protein / 400),
            "lip": float(energy * fat / 900), "gluc": float(energy * (100 - protein - fat) / 400),
            "fibres": float(number(fibres, "Fibres"))}


def estimate_energy(sex, age, height_cm, weight, activity=1.5, reduction_percent=10):
    age, height, weight, activity, reduction = [float(number(value, label, positive=True))
        for value, label in ((age, "Âge"), (height_cm, "Taille"), (weight, "Poids"), (activity, "Activité"))] + [float(number(reduction_percent, "Réduction"))]
    if not 18 <= age <= 100 or not 120 <= height <= 230 or not 30 <= weight <= 300:
        raise ValueError("Estimation réservée aux adultes : vérifiez l’âge, la taille et le poids.")
    if sex not in ("Homme", "Femme") or not 1.4 <= activity <= 2.4 or not 0 <= reduction <= 20:
        raise ValueError("Vérifiez l’équation, l’activité et la réduction (0 à 20 %).")
    bmi = weight / (height / 100) ** 2
    if reduction and bmi < 18.5:
        raise ValueError("L’outil ne propose pas de réduction calorique avec cet IMC. Demandez un objectif adapté à un professionnel.")
    rest = (1.083 if sex == "Homme" else 0.963) * weight ** .48 * (height / 100) ** .5 * age ** -.13 * 1000 / 4.1855
    maintenance = rest * activity
    return {"rest": rest, "maintenance": maintenance, "target": maintenance * (1 - reduction / 100)}


def today_paris():
    return datetime.now(ZoneInfo("Europe/Paris")).date()


def day_config(data):
    value = data.get("planning", {}).get(DAY_CONFIG_KEY, {})
    return {**value, "targets": {**DAY_DEFAULTS, **value.get("targets", {})},
            "snack": value.get("snack", True), "days": copy.deepcopy(value.get("days", {}))}


def meal_targets(targets, snack=True):
    weights = {key: value for key, value in MEAL_WEIGHTS.items() if snack or key != "Collation"}
    total = sum(weights.values())
    result = {key: {} for key in weights}
    for nutrient in ("kcal", *NUTRIENTS):
        target = number(targets.get(nutrient, 0), nutrient)
        allocated = Decimal(0)
        for index, (meal, weight) in enumerate(weights.items()):
            amount = (target - allocated if index == len(weights) - 1 else
                      min(target - allocated, (target * weight / total).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)))
            result[meal][nutrient] = amount
            allocated += amount
    return result


def day_totals(entries):
    active = [entry for entry in entries if not entry.get("annule") and not entry.get("retire")]
    totals = {"kcal": sum((number(entry["kcal"], "Calories") for entry in active), Decimal(0))}
    for key in NUTRIENTS:
        totals[key] = (sum((number(entry[key], key) for entry in active), Decimal(0))
                       if all(entry.get(key) is not None and not legacy_unknown_macros(entry) for entry in active) else None)
    return totals


def day_balance(targets, entries):
    consumed = day_totals(entries)
    return {key: None if consumed[key] is None else number(targets[key], key) - consumed[key]
            for key in ("kcal", *NUTRIENTS)}


def entry_from_recipe(name, recipe, net_weight, target, meal):
    if meal not in MEAL_WEIGHTS:
        raise ValueError("Choisissez un repas valide.")
    result = calculate_portion(recipe_total(recipe), net_weight, target)
    totals = nutrient_totals(recipe)
    entry = {"id": uuid4().hex, "nom": name, "repas": meal, "poids": float(result["grams"]),
             "kcal": float(result["actual_kcal"]), "nutrition_verified": True, "source_type": "recette"}
    for key in NUTRIENTS:
        entry[key] = None if totals[key] is None else float(totals[key] * result["grams"] / result["net"])
    return entry


def entry_from_food(name, food, grams, meal):
    if meal not in MEAL_WEIGHTS:
        raise ValueError("Choisissez un repas valide.")
    row = row_from_food(name, grams, food)
    return {"id": uuid4().hex, "nom": name, "repas": meal, "poids": row["poids"], "kcal": row["cal"],
            **{key: row.get(key) for key in NUTRIENTS}, "nutrition_verified": True, "source_type": "aliment"}


def record_consumption(entries, planned, actual_weight=None):
    """Un repas prévu n’est jamais mangé implicitement ; double clic sans double comptage."""
    if any(entry.get("plan_id") == planned["id"] and not entry.get("annule") for entry in entries):
        return copy.deepcopy(entries)
    factor = Decimal(1) if actual_weight is None else number(actual_weight, "Quantité mangée", positive=True) / number(planned["poids"], "Quantité prévue", positive=True)
    event = copy.deepcopy(planned)
    event.update(id=uuid4().hex, plan_id=planned["id"], heure=datetime.now(ZoneInfo("Europe/Paris")).strftime("%H:%M"))
    for key in ("poids", "kcal", *NUTRIENTS):
        if event.get(key) is not None:
            event[key] = float(number(event[key], key) * factor)
    return [*copy.deepcopy(entries), event]


def set_day_config(data, config):
    return {**copy.deepcopy(data.get("planning", {})), DAY_CONFIG_KEY: config}


def change_plan(data, day, entries):
    config = day_config(data)
    config["days"][day] = copy.deepcopy(entries)
    return set_day_config(data, config)


def validate_day_entry(entry):
    if not isinstance(entry, dict):
        raise ValueError("Repas invalide dans le journal ou le planning.")
    number(entry.get("kcal"), "Calories du repas")
    if entry.get("poids") is not None:
        number(entry["poids"], "Poids du repas")
    for key in NUTRIENTS:
        if entry.get(key) is not None:
            number(entry[key], key)


def legacy_unknown_macros(record, energy_key="kcal"):
    return (not record.get("nutrition_verified")
            and number(record.get(energy_key, 0), "Calories") > 0
            and all(record.get(key) is None or number(record[key], key) == 0 for key in NUTRIENTS))


def normalized_name(name):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", name.casefold())
                            if not unicodedata.combining(c)).split())


def parse_ingredient_list(text, library):
    """Ne jamais deviner un aliment ou ses calories ; tout ou rien."""
    index = {}
    for name in library:
        index.setdefault(normalized_name(name), []).append(name)
    rows, errors = [], []
    for line_number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip().lstrip("-• ")
        if not line:
            continue
        match = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*(?:g|grammes?)\s+(.+)", line, re.IGNORECASE)
        if match:
            weight, name = match.groups()
        else:
            parts = line.split(";")
            if len(parts) != 2:
                errors.append(f"Ligne {line_number} : format attendu « 150 g Riz cru » ou « Riz cru;150 ».")
                continue
            name, weight = parts
        matches = index.get(normalized_name(name.strip()), [])
        if len(matches) != 1:
            errors.append(f"Ligne {line_number} : ingrédient absent ou ambigu « {name.strip()} ». Créez sa fiche ou utilisez son nom exact.")
            continue
        try:
            rows.append(row_from_food(matches[0], weight, library[matches[0]]))
        except ValueError as error:
            errors.append(f"Ligne {line_number} : {error}")
    if not rows and not errors:
        errors.append("Collez au moins un ingrédient.")
    return ([] if errors else rows), errors


def food_values(value):
    """Les anciennes fiches ne renseignaient parfois que les calories."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = {"kcal": value}
    if not isinstance(value, dict):
        raise ValueError("Fiche ingrédient invalide.")
    result = {"kcal": float(number(value.get("kcal"), "Calories pour 100 g"))}
    legacy_unknown = legacy_unknown_macros(value)
    for key, label in NUTRIENTS.items():
        raw = None if legacy_unknown else value.get(key)
        result[key] = None if raw is None else float(number(raw, label))
        if result[key] is not None and result[key] > 100:
            raise ValueError(f"{label} : maximum 100 g pour 100 g d’aliment.")
    return result


def row_from_food(name, weight, food):
    values = food_values(food)
    row = ingredient_row(name, weight, values["kcal"], "pour 100 g")
    row["nutrition_verified"] = True
    factor = number(weight, "Quantité", positive=True) / 100
    for key in NUTRIENTS:
        if values[key] is not None:
            row[key] = float(number(values[key], key) * factor)
    return row


def nutrient_totals(recipe):
    rows = recipe.get("ingredients", [])
    result = {}
    for key in NUTRIENTS:
        if rows:
            result[key] = (sum((number(row[key], key) for row in rows), Decimal(0))
                           if all(row.get(key) is not None and not legacy_unknown_macros(row, "cal") for row in rows) else None)
        else:
            value = recipe.get(f"total_{key}")
            legacy_totals = {"kcal": recipe.get("total_cal", 0),
                             **{k: recipe.get(f"total_{k}") for k in NUTRIENTS},
                             "nutrition_verified": recipe.get("nutrition_verified", False)}
            if legacy_unknown_macros(legacy_totals):
                value = None
            result[key] = None if value is None else number(value, key)
    return result


def resize_row(row, weight):
    factor = number(weight, "Quantité", positive=True) / number(row["poids"], "Quantité initiale", positive=True)
    result = copy.deepcopy(row)
    result["poids"] = float(weight)
    for key in ("cal", *NUTRIENTS):
        if result.get(key) is not None:
            result[key] = float(number(result[key], key) * factor)
    return result


def edit_recipe_quantity():
    try:
        index = st.session_state.row_to_edit
        st.session_state._rows[index] = resize_row(st.session_state._rows[index], st.session_state.new_row_weight)
    except (ValueError, IndexError) as error:
        st.session_state._recipe_error = str(error)


def number(value, label, *, positive=False):
    """Valider aussi les données importées, pas seulement les widgets."""
    if isinstance(value, bool):
        raise ValueError(f"{label} : indiquez un nombre.")
    try:
        result = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} : indiquez un nombre valide.") from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        qualifier = "strictement positif" if positive else "positif ou nul"
        raise ValueError(f"{label} : le nombre doit être {qualifier}.")
    return result


def rounded(value, digits=0):
    return number(value, "Valeur").quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)


def readable_amount(value):
    text = f"{rounded(value, 1):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def calculate_portion(total_kcal, cooked_weight, target_kcal, tare=0):
    total = number(total_kcal, "Calories du plat", positive=True)
    gross = number(cooked_weight, "Poids cuit", positive=True)
    empty = number(tare, "Poids du contenant")
    target = number(target_kcal, "Calories de la portion", positive=True)
    net = gross - empty
    if net <= 0:
        raise ValueError("Le poids pesé doit dépasser le poids du contenant vide.")
    if target > total:
        raise ValueError(f"Le plat entier ne contient que {total.normalize():f} kcal. Réduisez la cible.")
    exact = net * target / total
    # Un poids entier ne doit jamais dépasser la quantité réellement disponible.
    grams = min(rounded(exact), net)
    if grams == 0:
        raise ValueError("La portion est inférieure à 0,5 g : augmentez la cible pour une pesée au gramme.")
    actual = grams * total / net
    return {"net": net, "exact": exact, "grams": grams, "actual_kcal": actual,
            "density": total * 100 / net, "remaining": net - grams}


def ingredient_row(name, weight, energy, basis):
    name = name.strip()
    if not name:
        raise ValueError("Donnez un nom à l’ingrédient.")
    weight = number(weight, "Quantité de l’ingrédient", positive=True)
    energy = number(energy, "Calories de l’ingrédient")
    calories = energy * weight / 100 if basis == "pour 100 g" else energy
    return {"nom": name, "poids": float(weight), "cal": float(calories)}


def recipe_total(recipe):
    rows = recipe.get("ingredients")
    if rows is not None and not isinstance(rows, list):
        raise ValueError("La liste des ingrédients est invalide.")
    # Les anciens totaux sont conservés pour les recettes sans détail.
    if rows:
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError("Un ingrédient est invalide.")
        return sum((number(row.get("cal"), "Calories d’un ingrédient") for row in rows), Decimal(0))
    return number(recipe.get("total_cal"), "Calories de la recette", positive=True)


def validate_data(data):
    if not isinstance(data, dict):
        raise ValueError("La sauvegarde doit contenir un objet JSON.")
    result = {}
    for key in ("recettes", "plats"):
        value = data.get(key, {})
        if not isinstance(value, dict):
            raise ValueError(f"La rubrique {key} est invalide.")
        result[key] = copy.deepcopy(value)
    for name, recipe in result["recettes"].items():
        if not isinstance(name, str) or not name.strip() or not isinstance(recipe, dict):
            raise ValueError("Une recette est invalide.")
        number(recipe_total(recipe), f"Calories de {name}", positive=True)
        rows = recipe.get("ingredients", [])
        if not isinstance(rows, list):
            raise ValueError(f"Ingrédients invalides : {name}.")
        for row in rows:
            if not isinstance(row, dict) or not str(row.get("nom", "")).strip():
                raise ValueError(f"Ingrédient invalide : {name}.")
            number(row.get("poids"), "Poids d’un ingrédient", positive=True)
            number(row.get("cal"), "Calories d’un ingrédient")
            for nutrient in NUTRIENTS:
                if row.get(nutrient) is not None:
                    number(row[nutrient], nutrient)
    for name, tare in result["plats"].items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Un contenant n’a pas de nom.")
        number(tare, "Tare du contenant")
    if "garde_manger" in data:
        if not isinstance(data["garde_manger"], dict):
            raise ValueError("Bibliothèque d’ingrédients invalide.")
        for name, values in data["garde_manger"].items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Un ingrédient n’a pas de nom.")
            food_values(values)
        result["garde_manger"] = copy.deepcopy(data["garde_manger"])
    for key in ("planning", "journal"):
        if key in data:
            if not isinstance(data[key], dict):
                raise ValueError(f"La rubrique {key} est invalide.")
            result[key] = copy.deepcopy(data[key])
    config = result.get("planning", {}).get(DAY_CONFIG_KEY)
    if config is not None:
        if not isinstance(config, dict) or not isinstance(config.get("days", {}), dict):
            raise ValueError("Configuration du planning invalide.")
        if not isinstance(config.get("targets", {}), dict) or not isinstance(config.get("snack", True), bool):
            raise ValueError("Repères de la journée invalides.")
        if config.get("nutrition_mode", "auto") not in ("auto", "manual"):
            raise ValueError("Mode de répartition invalide.")
        automatic_targets(1, config.get("protein_percent", 20), config.get("fat_percent", 35))
        if config.get("protein_basis", "percent") not in ("percent", "weight"):
            raise ValueError("Mode de calcul des protéines invalide.")
        if config.get("protein_basis") == "weight":
            number(config.get("body_weight"), "Poids actuel", positive=True)
            number(config.get("protein_per_kg", 2), "Protéines par kg", positive=True)
        for key, value in config.get("targets", {}).items():
            if key not in DAY_DEFAULTS:
                raise ValueError("Repère nutritionnel inconnu.")
            number(value, key, positive=(key == "kcal"))
        for day, entries in config.get("days", {}).items():
            date.fromisoformat(day)
            if not isinstance(entries, list):
                raise ValueError("Programme de journée invalide.")
            ids = set()
            for entry in entries:
                validate_day_entry(entry)
                if (not isinstance(entry.get("id"), str) or not entry["id"] or entry["id"] in ids
                        or entry.get("repas") not in MEAL_WEIGHTS or not isinstance(entry.get("nom"), str)
                        or not entry["nom"].strip()):
                    raise ValueError("Un repas prévu n’a pas d’identifiant ou de catégorie valide.")
                number(entry.get("poids"), "Poids prévu", positive=True)
                ids.add(entry["id"])
    for day, entries in result.get("journal", {}).items():
        date.fromisoformat(day)
        if not isinstance(entries, list):
            raise ValueError("Journal alimentaire invalide.")
        for entry in entries:
            validate_day_entry(entry)
    return result


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


@st.cache_resource
def cloud_sheet():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials
    credentials = ServiceAccountCredentials.from_json_keyfile_dict(
        dict(st.secrets["gcp_service_account"]),
        ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    return gspread.authorize(credentials).open("ProjetPoids_DB")


def read_cloud_key(key):
    raw = cloud_sheet().worksheet(key).acell("A1").value
    result = json.loads(raw) if raw else {}
    if not isinstance(result, dict):
        raise ValueError(f"La rubrique {key} est invalide.")
    return result


def boot():
    if st.session_state.get("_booted"):
        missing = [key for key in ("garde_manger", "planning", "journal") if key not in st.session_state._data]
        if st.session_state.get("_cloud_ready") and missing:
            try:
                extra = {key: read_cloud_key(key) for key in missing}
                validate_data({**st.session_state._data, **extra})
                st.session_state._data.update(extra)
                st.session_state._versions.update({key: fingerprint(value) for key, value in extra.items()})
            except Exception:
                st.error("Impossible de charger votre bibliothèque d’ingrédients. Rechargez la page pour réessayer.")
                st.stop()
        return
    try:
        configured = bool(st.secrets.get("gcp_service_account"))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        configured = False
    if configured:
        try:
            data = validate_data({key: read_cloud_key(key) for key in ("recettes", "plats", "garde_manger", "planning", "journal")})
        except Exception:
            st.error("Impossible de charger vos recettes. Aucune donnée n’a été remplacée. Réessayez plus tard.")
            if st.button("Réessayer le chargement"):
                cloud_sheet.clear()
                st.rerun()
            st.stop()
    else:
        data = {"recettes": {}, "plats": {}, "garde_manger": {}, "planning": {}, "journal": {}}
    st.session_state.update(_data=data, _cloud_ready=configured, _booted=True,
                            _versions={key: fingerprint(value) for key, value in data.items()})


def persist(key, value):
    """Ne changer la session qu’après confirmation de la sauvegarde distante."""
    proposal = copy.deepcopy(st.session_state._data)
    proposal[key] = value
    validate_data(proposal)
    if st.session_state._cloud_ready:
        try:
            remote = read_cloud_key(key)
            if fingerprint(remote) != st.session_state._versions.get(key, fingerprint({})):
                st.error("Les données ont changé dans une autre session. Exportez votre travail puis rechargez la page.")
                return False
            cloud_sheet().worksheet(key).update_acell("A1", json.dumps(value, ensure_ascii=False, allow_nan=False))
        except Exception:
            st.error("Sauvegarde non confirmée. Votre saisie reste disponible. Vérifiez la connexion avant de réessayer.")
            return False
    st.session_state._data = proposal
    st.session_state._versions[key] = fingerprint(value)
    return True


def calculator(data):
    st.subheader("Votre portion, en trois étapes")
    st.caption("Utilisez les calories de tous les ingrédients et le poids du plat entier après cuisson.")
    st.markdown("#### 1 · Calories du plat entier")
    source = st.radio("Comment connaissez-vous les calories ?", ["Saisie directe", "Recette enregistrée"], horizontal=True)
    total = 0
    selected_recipe = None
    if source == "Recette enregistrée":
        if not data["recettes"]:
            st.info("Aucune recette enregistrée. Utilisez la saisie directe ou créez une recette dans l’onglet Recettes.")
        else:
            name = st.selectbox("Recette", sorted(data["recettes"]))
            selected_recipe = data["recettes"][name]
            try:
                total = recipe_total(data["recettes"][name])
                st.metric("Calories du plat entier", f"{rounded(total, 1):f} kcal")
            except (ValueError, TypeError, KeyError):
                st.error("Cette recette contient des valeurs invalides. Corrigez-la avant de calculer une portion.")
    else:
        total = st.number_input("Calories du plat entier (kcal)", min_value=0.0, value=0.0, step=50.0,
                                help="Le total du plat, pas les calories pour 100 g.")
    st.markdown("#### 2 · Poids après cuisson")
    mode = st.radio("Ce qui est posé sur la balance", ["Nourriture seule / balance déjà tarée", "Plat avec son contenant"],
                    help="Si vous avez appuyé sur TARE avant d’ajouter la nourriture, choisissez la première option.")
    tare = 0
    if mode == "Plat avec son contenant":
        tare_source = st.radio("Poids du contenant vide", ["Saisir la tare", "Contenant enregistré"], horizontal=True)
        if tare_source == "Contenant enregistré" and data["plats"]:
            container = st.selectbox("Contenant", sorted(data["plats"]))
            tare = data["plats"][container]
            st.caption(f"Poids du contenant vide : {tare} g")
        else:
            if tare_source == "Contenant enregistré":
                st.info("Aucun contenant enregistré : indiquez son poids ci-dessous.")
            tare = st.number_input("Tare du contenant (g)", min_value=0.0, value=0.0, step=1.0)
    label = "Poids affiché sur la balance (g)" if mode == "Plat avec son contenant" else "Poids net du plat cuit (g)"
    weight = st.number_input(label, min_value=0.0, value=0.0, step=1.0, key=f"weight_{mode}")
    st.markdown("#### 3 · Calories souhaitées dans votre portion")
    if selected_recipe is not None:
        with st.expander("Choisir plutôt une quantité de protéines ou de glucides"):
            macro_target = st.selectbox("Calculer à partir de", ["Calories", "Protéines", "Glucides"])
            if macro_target != "Calories":
                nutrient_key = "prot" if macro_target == "Protéines" else "gluc"
                available = nutrient_totals(selected_recipe)[nutrient_key]
                desired = st.number_input(f"{macro_target} souhaités (g)", min_value=0.0, value=0.0, step=1.0)
                if available is None or available <= 0:
                    st.info("Complétez ce nutriment pour tous les ingrédients de la recette avant d’utiliser ce mode.")
                    return
                if desired <= 0 or number(desired, "Quantité") > available:
                    st.info(f"Choisissez une quantité supérieure à 0 et au plus égale aux {rounded(available, 1):f} g du plat entier.")
                    return
                target_from_macro = number(desired, "Quantité") * total / available
            else:
                target_from_macro = None
    else:
        target_from_macro = None
    if target_from_macro is None:
        target = st.number_input("Calories de la portion (kcal)", min_value=0.0, value=600.0, step=25.0)
    else:
        target = target_from_macro
        st.metric("Calories correspondant à cette quantité", f"{rounded(target, 1):f} kcal")
    try:
        result = calculate_portion(total, weight, target, tare)
    except ValueError as error:
        st.info(str(error))
        return
    st.success(f"Servez-vous {result['grams']:f} g de nourriture cuite")
    st.caption(f"Cette quantité apporte environ {rounded(result['actual_kcal'], 1):f} kcal. "
               "Le poids est arrondi au gramme le plus proche.")
    c1, c2, c3 = st.columns(3)
    c1.metric("Poids net du plat", f"{result['net']:f} g")
    c2.metric("Pour 100 g", f"{rounded(result['density'], 1):f} kcal")
    c3.metric("Reste après cette portion", f"{result['remaining']:f} g")
    if selected_recipe is not None:
        st.markdown("#### Dans votre portion")
        totals = nutrient_totals(selected_recipe)
        for column, (key, label) in zip(st.columns(4), NUTRIENTS.items()):
            value = totals[key]
            column.metric(label, "Non renseigné" if value is None else
                          f"{rounded(value * result['grams'] / result['net'], 1):f} g")
        if any(value is None for value in totals.values()):
            st.caption("Une donnée manquante n’est pas un zéro. Complétez les fiches d’ingrédients pour connaître tous les apports.")
    else:
        st.caption("Pour voir aussi les protéines, glucides, lipides et fibres, utilisez une recette avec ses ingrédients renseignés.")
    with st.expander("Vérifier le calcul"):
        st.write(f"Poids net = {weight} − {tare} = {result['net']:f} g")
        st.write(f"Portion = {result['net']:f} × {target} ÷ {total} = {rounded(result['exact'], 2):f} g")
        st.caption("Le calcul suppose que les ingrédients sont répartis de façon homogène dans le plat. "
                   "Les calories restent une estimation fondée sur les quantités et étiquettes renseignées.")


def recipes(data):
    st.subheader("Vos recettes")
    st.caption("Additionnez les calories des ingrédients utilisés avant cuisson. Le poids cuit sera demandé au moment de servir.")
    choice = st.selectbox("Recette à préparer", ["Nouvelle recette"] + sorted(data["recettes"]))
    if st.session_state.get("_editing") != choice:
        st.session_state._editing = choice
        st.session_state._rows = copy.deepcopy(data["recettes"].get(choice, {}).get("ingredients", []))
        st.session_state.recipe_name = "" if choice == "Nouvelle recette" else choice
    name = st.text_input("Nom de la recette", key="recipe_name")
    if st.session_state.get("_recipe_error"):
        st.error(st.session_state.pop("_recipe_error"))
    library = data.get("garde_manger", {})
    if library:
        st.markdown("#### Ajout rapide : ingrédient + grammes")
        with st.form("quick_add", clear_on_submit=False):
            chosen = st.selectbox("Mes ingrédients", sorted(library))
            quick_weight = st.number_input("Grammes à ajouter", min_value=0.0, value=100.0, step=1.0)
            if st.form_submit_button("Ajouter à la recette"):
                try:
                    st.session_state._rows.append(row_from_food(chosen, quick_weight, library[chosen]))
                except ValueError as error:
                    st.error(str(error))
        with st.expander("Ajouter plusieurs ingrédients d’un coup"):
            pasted = st.text_area("Liste d’ingrédients", placeholder="200 g Riz cru\n150 g Poulet")
            if st.button("Ajouter toute la liste"):
                parsed, errors = parse_ingredient_list(pasted, library)
                if errors:
                    for error in errors:
                        st.error(error)
                else:
                    st.session_state._rows.extend(parsed)
    st.caption("Un ingrédient absent ? Ajoutez-le une seule fois dans l’onglet Ingrédients, ou utilisez la saisie libre ci-dessous.")
    with st.expander("Saisie libre d’un ingrédient", expanded=not bool(library)):
        with st.form("ingredient", clear_on_submit=False):
            ing = st.text_input("Nom de l’ingrédient")
            weight = st.number_input("Quantité utilisée (g)", min_value=0.0, step=1.0)
            basis = st.radio("Les calories indiquées sont", ["pour 100 g", "pour la quantité utilisée"], horizontal=True)
            energy = st.number_input("Calories indiquées (kcal)", min_value=0.0, step=1.0)
            if st.form_submit_button("Ajouter l’ingrédient"):
                try:
                    st.session_state._rows.append(ingredient_row(ing, weight, energy, basis))
                except ValueError as error:
                    st.error(str(error))
    rows = st.session_state._rows
    if rows:
        st.table([{"Ingrédient": row["nom"], "Quantité (g)": row["poids"],
                   "Calories (kcal)": float(rounded(row["cal"], 2))} for row in rows])
        remove = st.selectbox("Ingrédient à retirer", range(len(rows)),
                              format_func=lambda i: f"{i + 1} · {rows[i]['nom']}", key="row_to_edit")
        with st.form("change_weight"):
            st.number_input("Nouvelle quantité (g)", min_value=0.0, value=100.0, step=1.0, key="new_row_weight")
            st.form_submit_button("Modifier la quantité sélectionnée", on_click=edit_recipe_quantity)
        if st.button("Retirer cet ingrédient"):
            rows.pop(remove)
            st.rerun()
        total = sum(number(row["cal"], "Calories") for row in rows)
    else:
        st.info("Ajoutez les ingrédients du plat, ou indiquez directement son total calorique ci-dessous.")
        old = data["recettes"].get(choice, {}).get("total_cal", 0)
        total = st.number_input("Total calorique sans détail (kcal)", min_value=0.0,
                                value=float(old), step=1.0, key=f"manual_{choice}")
    st.metric("Calories de la recette entière", f"{rounded(total, 1):f} kcal")
    overwrite = name.strip() in data["recettes"] and name.strip() != choice
    approved = st.checkbox("Remplacer la recette existante portant ce nom") if overwrite else True
    if st.button("Enregistrer la recette", type="primary", disabled=not approved):
        if not name.strip():
            st.error("Donnez un nom à la recette.")
        elif number(total, "Calories") <= 0:
            st.error("La recette doit contenir plus de 0 kcal.")
        else:
            updated = copy.deepcopy(data["recettes"])
            previous = updated.get(choice, {})
            value = {"total_cal": float(total), "ingredients": copy.deepcopy(rows), "nutrition_verified": True}
            # Aucun macronutriment manquant n’est présenté comme un zéro connu.
            for field, row_key in ((f"total_{key}", key) for key in NUTRIENTS):
                if rows and all(row_key in row for row in rows):
                    value[field] = sum(float(row[row_key]) for row in rows)
                elif not rows and field in previous:
                    value[field] = previous[field]
            updated[name.strip()] = value
            if persist("recettes", updated):
                st.session_state._notice = ("Recette enregistrée." if st.session_state._cloud_ready else
                    "Recette disponible pour cette session. Exportez vos données pour la conserver.")
                st.rerun()


def containers(data):
    st.subheader("Vos contenants vides")
    st.caption("Pesez chaque contenant vide. Sa tare ne sera retirée que si vous choisissez « Plat avec son contenant ».")
    with st.form("container"):
        name = st.text_input("Nom du contenant")
        weight = st.number_input("Poids du contenant vide (g)", min_value=0.0, step=1.0)
        if st.form_submit_button("Enregistrer le contenant"):
            if not name.strip() or weight <= 0:
                st.error("Indiquez un nom et un poids strictement positif.")
            elif name.strip() in data["plats"]:
                st.error("Ce nom existe déjà. Utilisez un nouveau nom pour éviter un remplacement involontaire.")
            else:
                updated = {**data["plats"], name.strip(): weight}
                if persist("plats", updated):
                    st.session_state._notice = "Contenant enregistré."
                    st.rerun()
    current = st.session_state._data["plats"]
    if current:
        st.table([{"Contenant": name, "Poids vide (g)": weight} for name, weight in current.items()])


def backup(data):
    st.subheader("Conserver vos recettes et contenants")
    st.download_button("Télécharger ma sauvegarde", json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False),
                       file_name="portionneur-sauvegarde.json", mime="application/json")
    st.caption("La sauvegarde contient vos recettes, ingrédients, contenants, repères, programmes et journal alimentaire. Les anciennes données de planning et de journal sont conservées.")
    uploaded = st.file_uploader("Restaurer une sauvegarde JSON", type=["json"])
    if uploaded is not None:
        try:
            if uploaded.size > 1_000_000:
                raise ValueError("La sauvegarde dépasse la limite de 1 Mo.")
            incoming = validate_data(json.loads(uploaded.getvalue().decode("utf-8-sig")))
            st.write(f"{len(incoming['recettes'])} recette(s) et {len(incoming['plats'])} contenant(s) à importer.")
            st.caption("Les éléments nouveaux sont ajoutés. Les noms déjà présents sont remplacés après votre confirmation.")
            confirmed = st.checkbox("J’ai téléchargé une sauvegarde actuelle et je confirme l’importation")
            if st.button("Importer les recettes et contenants", disabled=not confirmed):
                for key in incoming:
                    merged = {**st.session_state._data.get(key, {}), **incoming[key]}
                    if not persist(key, merged):
                        st.warning("Importation interrompue. Vérifiez les rubriques avant de réessayer.")
                        break
                else:
                    st.session_state._notice = "Importation terminée. Les données sont prêtes dans les autres onglets."
                    st.rerun()
        except (ValueError, TypeError, KeyError, UnicodeError):
            st.error("Sauvegarde invalide. Aucune donnée n’a été importée.")


def ingredients(data):
    st.subheader("Mes ingrédients : une seule saisie")
    st.caption("Reprenez les valeurs pour 100 g de l’étiquette, dans le même état que la quantité pesée (cru, cuit, égoutté…).")
    library = data.get("garde_manger", {})
    selection = st.selectbox("Fiche ingrédient", ["Nouvel ingrédient"] + sorted(library))
    old = food_values(library[selection]) if selection in library else {"kcal": 0.0, **{k: None for k in NUTRIENTS}}
    with st.form(f"food_{selection}"):
        name = st.text_input("Nom précis de l’ingrédient", value="" if selection == "Nouvel ingrédient" else selection,
                             help="Par exemple : riz cru, marque du paquet.")
        kcal = st.number_input("kcal pour 100 g", min_value=0.0, value=float(old["kcal"]), step=1.0)
        values = {}
        with st.expander("Protéines, glucides, lipides et fibres (facultatif)"):
            st.caption("Laissez vide si l’étiquette ne renseigne pas la valeur. Saisissez 0 uniquement si la valeur est connue.")
            for key, label in NUTRIENTS.items():
                values[key] = st.number_input(f"{label} pour 100 g", min_value=0.0, max_value=100.0,
                                             value=old[key], step=0.1, format="%.1f")
        source = st.text_input("Source (facultatif)", value=str(library.get(selection, {}).get("source", ""))
                               if isinstance(library.get(selection), dict) else "", placeholder="Étiquette du paquet ou référence Ciqual")
        confirmed = st.checkbox("Remplacer la fiche portant le même nom")
        if st.form_submit_button("Enregistrer l’ingrédient"):
            name = name.strip()
            if not name or name == "Nouvel ingrédient":
                st.error("Donnez un nom précis à l’ingrédient.")
            elif name in library and name != selection and not confirmed:
                st.error("Ce nom existe déjà. Confirmez le remplacement ou choisissez un autre nom.")
            else:
                record = {"kcal": kcal, **values, "source": source.strip(), "nutrition_verified": True}
                if persist("garde_manger", {**library, name: record}):
                    st.session_state._notice = "Ingrédient mémorisé : il est prêt dans l’ajout rapide des recettes."
                    st.rerun()
    if library:
        st.caption("Modifier une fiche n’altère pas les recettes déjà enregistrées : leurs valeurs restent celles utilisées lors de leur création.")
    st.link_button("Consulter les aliments de référence Ciqual", "https://ciqual.anses.fr/")


def day_table(entries):
    return [{"Repas": item.get("repas", "Autre"), "Aliment / recette": item.get("nom", item.get("recette", "Repas")),
             "Poids (g)": "—" if item.get("poids") is None else readable_amount(item["poids"]), "kcal": readable_amount(item["kcal"]),
             **{label + " (g)": "À compléter" if item.get(key) is None or legacy_unknown_macros(item)
                else readable_amount(item[key]) for key, label in NUTRIENTS.items()}}
            for item in entries if not item.get("annule") and not item.get("retire")]


def day_dashboard(data):
    st.subheader("Ma journée : prévoir, manger, ajuster")
    selected_date = st.date_input("Journée", value=today_paris(), key="day_selected_date")
    day = selected_date.isoformat()
    config = day_config(data)
    targets = config["targets"]
    planned = config["days"].get(day, [])
    journal = data.get("journal", {})
    eaten = journal.get(day, [])
    live_plans = [entry for entry in planned if not entry.get("retire")]
    allocations = meal_targets(targets, config["snack"])

    st.info(f"Mon objectif enregistré : {readable_amount(targets['kcal'])} kcal par jour · "
            f"{readable_amount(targets['prot'])} g de protéines · {readable_amount(targets['gluc'])} g de glucides · "
            f"{readable_amount(targets['lip'])} g de lipides · {readable_amount(targets['fibres'])} g de fibres")

    with st.expander("Mes repères quotidiens et ma collation"):
        source = st.radio("Comment choisir mes calories ?", ["J’ai déjà un objectif calorique", "Estimer avec mon profil"])
        if source == "Estimer avec mon profil":
            st.caption("Profil utilisé dans cette session uniquement. Estimation adulte par l’équation de Black ; le maintien réel varie avec l’activité et l’évolution du poids.")
            sex = st.selectbox("Équation", ["Homme", "Femme"])
            age = st.number_input("Âge (ans)", min_value=18, max_value=100, value=30)
            height = st.number_input("Taille (cm)", min_value=120.0, max_value=230.0, value=170.0)
            weight = st.number_input("Poids actuel (kg)", min_value=30.0, max_value=300.0, value=70.0)
            activity = st.selectbox("Activité habituelle", [1.4, 1.5, 1.6, 1.8, 2.0, 2.4], index=1,
                format_func=lambda value: {1.4: "Très peu de déplacements (1,4)", 1.5: "Entre journées assises et petits déplacements (1,5)",
                1.6: "Petits déplacements réguliers (1,6)", 1.8: "Activité importante (1,8)", 2.0: "Travail physique intense (2,0)", 2.4: "Activité très intense (2,4)"}[value])
            reduction = st.number_input("Réduction choisie par rapport au maintien (%)", min_value=0.0, max_value=20.0, value=10.0, step=1.0)
            st.caption("0 % correspond au maintien estimé. Une réduction est un choix de départ à réévaluer, sans garantie de rythme de perte de poids.")
            try:
                estimate = estimate_energy(sex, age, height, weight, activity, reduction)
                proposed = float(Decimal(str(estimate['target'] / 50)).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * 50)
                st.write(f"Maintien estimé : {estimate['maintenance']:.0f} kcal/j · objectif proposé : {proposed:.0f} kcal/j")
                if st.button("Utiliser cette estimation"):
                    st.session_state.day_goal_kcal = proposed
            except ValueError as exc:
                st.error(str(exc))
            st.markdown("Méthode : [équation de Black et niveaux d’activité (AFDN)](https://www.afdn.org/sites/www.afdn.org/files/documentation/2021-11/Besoins%20et%20apports%20nutritionnels.pdf)")
        energy = st.number_input("Repère calories par jour (kcal)", min_value=1.0, value=float(targets["kcal"]), step=50.0, key="day_goal_kcal")
        mode = st.radio("Répartition des macronutriments", ["Automatique selon mes calories", "Saisie manuelle en grammes"],
                        index=1 if config.get("nutrition_mode") == "manual" else 0)
        new_targets = None
        protein_percent, fat_percent = config.get("protein_percent", 20.0), config.get("fat_percent", 35.0)
        protein_basis = config.get("protein_basis", "percent")
        body_weight, protein_per_kg = config.get("body_weight", 70.0), config.get("protein_per_kg", 2.0)
        if mode == "Automatique selon mes calories":
            basis_choice = st.radio("Calcul des protéines", ["Selon mon poids (g/kg)", "En pourcentage des calories"], index=0 if protein_basis == "weight" else 1)
            protein_basis = "weight" if basis_choice == "Selon mon poids (g/kg)" else "percent"
            if protein_basis == "weight":
                body_weight = st.number_input("Poids de référence actuel (kg)", min_value=1.0, value=float(body_weight), step=0.5)
                protein_per_kg = st.number_input("Protéines par kilo de poids de corps (g/kg/j)", min_value=0.1, value=float(protein_per_kg), step=0.1)
                st.caption("Ces réglages de poids et de protéines sont conservés avec vos objectifs. Si les calories changent, les protéines restent liées au poids ; les glucides s’ajustent.")
            else:
                protein_percent = st.number_input("Part des protéines (%)", min_value=0.0, max_value=100.0, value=float(protein_percent), step=1.0)
            st.caption("Les glucides complètent les calories après les protéines et les lipides. Conversion : 4 kcal/g pour protéines et glucides, 9 kcal/g pour lipides ; fibres suivies séparément.")
            fat_percent = st.number_input("Part des lipides (%)", min_value=0.0, max_value=100.0, value=float(fat_percent), step=1.0)
            fibre_goal = st.number_input("Repère fibres par jour (g)", min_value=0.0, value=float(targets["fibres"]), step=1.0)
            try:
                new_targets = automatic_targets(energy, protein_percent, fat_percent, fibre_goal,
                    body_weight=body_weight if protein_basis == "weight" else None, protein_per_kg=protein_per_kg)
                st.write(f"Aperçu : {new_targets['prot']:.1f} g de protéines · {new_targets['gluc']:.1f} g de glucides · {new_targets['lip']:.1f} g de lipides")
            except ValueError as exc:
                st.error(str(exc))
        else:
            new_targets = {"kcal": energy}
            for key, label in NUTRIENTS.items():
                new_targets[key] = st.number_input(f"Repère {label.lower()} par jour (g)", min_value=0.0,
                                                    value=float(targets[key]), step=1.0)
            macro_energy = 4 * (new_targets['prot'] + new_targets['gluc']) + 9 * new_targets['lip']
            if abs(macro_energy - energy) > max(50, energy * .05):
                st.warning(f"Vos macros représentent environ {macro_energy:.0f} kcal, contre {energy:.0f} kcal choisies. Vérifiez la cohérence de votre saisie.")
        snack = st.checkbox("Prévoir une collation facultative", value=config["snack"])
        st.caption("L’aperçu ne remplace votre objectif qu’après « Enregistrer mes repères ». Les portions déjà prévues ou mangées restent inchangées.")
        if st.button("Enregistrer mes repères", disabled=new_targets is None):
            changed = {**config, "targets": new_targets, "snack": snack,
                       "nutrition_mode": "manual" if mode == "Saisie manuelle en grammes" else "auto",
                       "protein_percent": protein_percent, "fat_percent": fat_percent,
                       "protein_basis": protein_basis, "body_weight": body_weight, "protein_per_kg": protein_per_kg}
            if persist("planning", set_day_config(data, changed)):
                st.session_state._notice = "Repères enregistrés. Le bilan reflète vos objectifs actuels ; les repas déjà saisis sont conservés."
                st.rerun()
        st.caption("Conseil : raisonnez sur l’ensemble de la journée et variez les aliments ; atteindre les macros ne garantit pas à lui seul tous les apports nécessaires.")
    st.markdown("#### Mes repères par repas")
    st.table([{"Repas": meal, "Calories (kcal)": readable_amount(goal["kcal"]),
               "Protéines (g)": readable_amount(goal["prot"]), "Glucides (g)": readable_amount(goal["gluc"]),
               "Lipides (g)": readable_amount(goal["lip"])} for meal, goal in allocations.items()])
    st.caption("Estimation souple selon votre objectif enregistré : vous pouvez répartir autrement selon votre faim et vos horaires. Le total de la journée reste le repère.")
    totals = day_totals(eaten)
    balances = day_balance(targets, eaten)
    predicted = day_totals(live_plans)
    st.markdown("#### Ce que j’ai réellement mangé")
    for column, key in zip(st.columns(4), ("kcal", "prot", "gluc", "lip")):
        label = "Calories" if key == "kcal" else NUTRIENTS[key]
        unit = "kcal" if key == "kcal" else "g"
        value, remaining = totals[key], balances[key]
        column.metric(label, "Incomplet" if value is None else f"{rounded(value, 1):f} {unit}")
        if remaining is None:
            column.caption("Reste indéterminé : données manquantes")
        elif remaining < 0:
            column.caption(f"Au-dessus du repère : {rounded(-remaining, 1):f} {unit}")
        else:
            column.caption(f"Reste par rapport au repère : {rounded(remaining, 1):f} {unit}")
    st.progress(float(min(Decimal(1), max(Decimal(0), totals["kcal"] / number(targets["kcal"], "Objectif", positive=True)))))
    fibre_total = totals["fibres"]
    st.caption("Fibres : données incomplètes" if fibre_total is None else
               f"Fibres consommées : {rounded(fibre_total, 1):f} g · repère {targets['fibres']} g")
    if any(value is None for value in totals.values()):
        st.info("Un nutriment manque pour au moins un aliment mangé : son total et son reste ne peuvent pas être calculés de façon fiable. Ce n’est pas un zéro.")
    st.caption("Seuls les repas marqués comme mangés sont décomptés. Un dépassement ne modifie pas automatiquement les autres repas et n’est pas une consigne de sauter un repas.")

    with st.expander("Journée type : une organisation souple"):
        ideas = {"Petit déjeuner": "Ex. produit laitier ou alternative + fruit + pain ou céréales",
                 "Déjeuner": "Ex. source de protéines + féculent + légumes + matière grasse",
                 "Dîner": "Ex. source de protéines + féculent selon le repas + légumes",
                 "Collation": "Facultative : selon la faim et le reste de la journée"}
        st.table([{"Repas": meal, "Exemple d’organisation": ideas[meal]} for meal in allocations])
        st.caption("La répartition par repas est indicative : l’ensemble de la journée compte. Sans collation, les repères sont redistribués sur les trois repas ; les repas déjà prévus restent inchangés.")
        if data["recettes"]:
            with st.form("make_day_type"):
                choices = {}
                for meal, goal in allocations.items():
                    choices[meal] = (
                        st.selectbox(f"Recette pour {meal.lower()}", ["À choisir"] + sorted(data["recettes"])),
                        st.number_input(f"Poids net cuit du plat entier — {meal.lower()} (g)", min_value=0.0, step=1.0))
                replace = st.checkbox("Remplacer le programme prévu pour cette date") if live_plans else True
                st.caption("Cette proposition répartit les calories entre vos recettes et affiche les macros obtenues ; elle ne garantit pas de satisfaire tous les repères nutritionnels.")
                if st.form_submit_button("Calculer et prévoir cette journée"):
                    try:
                        if live_plans and not replace:
                            raise ValueError("Un programme existe déjà. Confirmez son remplacement ou ajoutez un repas individuellement.")
                        proposals = []
                        for meal, (recipe_name, cooked) in choices.items():
                            if recipe_name == "À choisir":
                                raise ValueError(f"Choisissez une recette pour {meal.lower()}.")
                            proposals.append(entry_from_recipe(recipe_name, data["recettes"][recipe_name], cooked, allocations[meal]["kcal"], meal))
                        old = [{**entry, "retire": True} for entry in planned]
                        if persist("planning", change_plan(data, day, old + proposals)):
                            st.session_state._notice = "Journée prévue. Rien n’a encore été ajouté aux repas mangés."
                            st.rerun()
                    except ValueError as error:
                        st.error(str(error))
    st.markdown("#### Prévoir ou ajouter un repas")
    meal = st.selectbox("Repas à préparer", list(allocations), key="day_meal")
    if data["recettes"]:
        with st.expander("Une portion de mes recettes"):
            with st.form(f"day_recipe_{meal}"):
                name = st.selectbox("Recette à prévoir", sorted(data["recettes"]))
                cooked = st.number_input("Poids net cuit du plat entier pour ce repas (g)", min_value=0.0, step=1.0)
                kcal = st.number_input("Calories prévues pour cette portion", min_value=0.0, value=float(allocations[meal]["kcal"]), step=25.0)
                if st.form_submit_button("Prévoir cette portion"):
                    try:
                        entry = entry_from_recipe(name, data["recettes"][name], cooked, kcal, meal)
                        if persist("planning", change_plan(data, day, [*planned, entry])):
                            st.session_state._notice = "Portion prévue. Marquez-la comme mangée quand vous la consommez."
                            st.rerun()
                    except ValueError as error:
                        st.error(str(error))
    library = data.get("garde_manger", {})
    if library:
        with st.expander("Un aliment de ma liste"):
            with st.form(f"day_food_{meal}"):
                name = st.selectbox("Aliment à prévoir", sorted(library))
                grams = st.number_input("Quantité prévue de cet aliment (g)", min_value=0.0, value=100.0, step=1.0)
                if st.form_submit_button("Prévoir cet aliment"):
                    try:
                        entry = entry_from_food(name, library[name], grams, meal)
                        if persist("planning", change_plan(data, day, [*planned, entry])):
                            st.session_state._notice = "Aliment prévu. Il n’est pas encore décompté comme mangé."
                            st.rerun()
                    except ValueError as error:
                        st.error(str(error))
    if not library and not data["recettes"]:
        st.info("Créez une fiche dans Ingrédients ou une recette pour commencer le programme de la journée.")

    st.markdown("#### Mon programme prévu")
    if live_plans:
        st.table(day_table(live_plans))
        st.caption(f"Programme entier : {rounded(predicted['kcal'], 1):f} kcal. Ce total inclut aussi les portions déjà marquées mangées.")
        planned_gap = day_balance(targets, live_plans)
        summary = []
        for key in ("kcal", *NUTRIENTS):
            gap = planned_gap[key]
            label = "kcal" if key == "kcal" else NUTRIENTS[key].lower()
            summary.append(f"{label} : données incomplètes" if gap is None else
                           f"{label} : {'au-dessus' if gap < 0 else 'en dessous'} du repère de {rounded(abs(gap), 1):f}")
        st.caption("Comparaison du programme aux repères — " + " · ".join(summary))
        for entry in live_plans:
            already = any(event.get("plan_id") == entry["id"] and not event.get("annule") for event in eaten)
            with st.expander(f"{entry['repas']} · {entry['nom']} · {entry['poids']:g} g" + (" · mangé" if already else "")):
                actual = st.number_input("Quantité réellement mangée (g)", min_value=0.0, value=float(entry["poids"]),
                                         step=1.0, key=f"actual_{entry['id']}")
                if st.button("J’ai mangé cette portion", key=f"eat_{entry['id']}", disabled=already or selected_date > today_paris()):
                    try:
                        new_entries = record_consumption(eaten, entry, actual)
                        if persist("journal", {**journal, day: new_entries}):
                            st.session_state._notice = "Portion ajoutée à ce que vous avez réellement mangé."
                            st.rerun()
                    except ValueError as error:
                        st.error(str(error))
                if st.button("Retirer du programme prévu", key=f"unplan_{entry['id']}"):
                    modified = [{**item, "retire": True} if item["id"] == entry["id"] else item for item in planned]
                    if persist("planning", change_plan(data, day, modified)):
                        st.rerun()
        if selected_date > today_paris():
            st.caption("La consommation ne peut être enregistrée que pour aujourd’hui ou une journée passée.")
    else:
        st.info("Aucun repas prévu pour cette date. Le bilan consommé reste indépendant de votre programme.")
    removed = [entry for entry in planned if entry.get("retire")]
    if removed:
        with st.expander("Portions retirées : restaurer le programme"):
            for entry in removed:
                if st.button(f"Restaurer au programme : {entry['nom']}", key=f"restore_plan_{entry['id']}"):
                    restored = [{**item, "retire": False} if item["id"] == entry["id"] else item for item in planned]
                    if persist("planning", change_plan(data, day, restored)):
                        st.rerun()
    active = [event for event in eaten if not event.get("annule")]
    if active:
        st.markdown("#### Mon journal des repas mangés")
        st.table(day_table(active))
        for index, event in enumerate(eaten):
            if event.get("annule"):
                continue
            if st.button(f"Annuler l’enregistrement : {event.get('nom', event.get('recette', 'Repas'))}", key=f"cancel_eat_{day}_{index}"):
                changed = copy.deepcopy(eaten)
                changed[index]["annule"] = True
                if persist("journal", {**journal, day: changed}):
                    st.rerun()
    cancelled = [index for index, event in enumerate(eaten) if event.get("annule")]
    if cancelled:
        with st.expander("Enregistrements annulés : restaurer une saisie"):
            for index in cancelled:
                event = eaten[index]
                duplicate = event.get("plan_id") is not None and any(item.get("plan_id") == event["plan_id"] and not item.get("annule") for item in eaten)
                if st.button(f"Restaurer {event.get('nom', event.get('recette', 'Repas'))}", key=f"restore_{day}_{index}", disabled=duplicate or selected_date > today_paris()):
                    changed = copy.deepcopy(eaten)
                    changed[index]["annule"] = False
                    if persist("journal", {**journal, day: changed}):
                        st.rerun()


def main():
    st.set_page_config(page_title="Le Portionneur", page_icon="🥝", layout="centered")
    st.title("🥝 Le Portionneur")
    st.write("Vos portions, vos repas et votre bilan au quotidien.")
    boot()
    if st.session_state.get("_notice"):
        st.success(st.session_state.pop("_notice"))
    if st.session_state._cloud_ready:
        st.caption("Vos recettes et contenants sont reliés à votre sauvegarde en ligne.")
    else:
        st.warning("Mode local : vos recettes restent dans cette session uniquement. Téléchargez une sauvegarde avant de quitter.")
    tabs = st.tabs(["Ma journée", "Ma portion", "Recettes", "Ingrédients", "Contenants", "Sauvegarde"])
    data = st.session_state._data
    with tabs[0]:
        day_dashboard(data)
    with tabs[1]:
        calculator(data)
    with tabs[2]:
        recipes(data)
    with tabs[3]:
        ingredients(data)
    with tabs[4]:
        containers(data)
    with tabs[5]:
        backup(st.session_state._data)


if __name__ == "__main__":
    main()
