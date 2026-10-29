"""Le Portionneur — calculs purs et interface Streamlit centrée sur les portions."""
import copy
import hashlib
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import streamlit as st


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
    for name, tare in result["plats"].items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Un contenant n’a pas de nom.")
        number(tare, "Tare du contenant")
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
        return
    try:
        configured = bool(st.secrets.get("gcp_service_account"))
    except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
        configured = False
    if configured:
        try:
            data = validate_data({key: read_cloud_key(key) for key in ("recettes", "plats")})
        except Exception:
            st.error("Impossible de charger vos recettes. Aucune donnée n’a été remplacée. Réessayez plus tard.")
            if st.button("Réessayer le chargement"):
                cloud_sheet.clear()
                st.rerun()
            st.stop()
    else:
        data = {"recettes": {}, "plats": {}}
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
            if fingerprint(remote) != st.session_state._versions[key]:
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
    if source == "Recette enregistrée":
        if not data["recettes"]:
            st.info("Aucune recette enregistrée. Utilisez la saisie directe ou créez une recette dans l’onglet Recettes.")
        else:
            name = st.selectbox("Recette", sorted(data["recettes"]))
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
    target = st.number_input("Calories de la portion (kcal)", min_value=0.0, value=600.0, step=25.0)
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
                              format_func=lambda i: f"{i + 1} · {rows[i]['nom']}")
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
            value = {"total_cal": float(total), "ingredients": copy.deepcopy(rows)}
            # Aucun macronutriment manquant n’est présenté comme un zéro connu.
            for field, row_key in (("total_prot", "prot"), ("total_gluc", "gluc"), ("total_lip", "lip")):
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
    st.caption("La sauvegarde contient les recettes et les contenants. Elle n’inclut pas les anciens journaux ou plannings.")
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
                for key in ("recettes", "plats"):
                    merged = {**st.session_state._data[key], **incoming[key]}
                    if not persist(key, merged):
                        st.warning("Importation interrompue. Vérifiez les rubriques avant de réessayer.")
                        break
                else:
                    st.session_state._notice = "Importation terminée. Les données sont prêtes dans les autres onglets."
                    st.rerun()
        except (ValueError, TypeError, KeyError, UnicodeError):
            st.error("Sauvegarde invalide. Aucune donnée n’a été importée.")


def main():
    st.set_page_config(page_title="Le Portionneur", page_icon="🥝", layout="centered")
    st.title("🥝 Le Portionneur")
    st.write("Le bon poids pour les calories que vous choisissez.")
    boot()
    if st.session_state.get("_notice"):
        st.success(st.session_state.pop("_notice"))
    if st.session_state._cloud_ready:
        st.caption("Vos recettes et contenants sont reliés à votre sauvegarde en ligne.")
    else:
        st.warning("Mode local : vos recettes restent dans cette session uniquement. Téléchargez une sauvegarde avant de quitter.")
    tabs = st.tabs(["Ma portion", "Recettes", "Contenants", "Sauvegarde"])
    data = st.session_state._data
    with tabs[0]:
        calculator(data)
    with tabs[1]:
        recipes(data)
    with tabs[2]:
        containers(data)
    with tabs[3]:
        backup(st.session_state._data)


if __name__ == "__main__":
    main()
