import streamlit as st
import json
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time
import requests
from fpdf import FPDF
import io

# ==============================================================================
# CONFIG & CONNEXION
# ==============================================================================
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

@st.cache_resource
def get_gspread_client():
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

TABS_MAPPING = {
    "recettes": "recettes",
    "plats": "plats",
    "planning": "planning",
    "garde_manger": "garde_manger",
    "journal": "journal",
    "poids": "poids"
}

# --- OUTILS EXTERNES ---
def search_openfoodfacts(query):
    url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={query}&search_simple=1&action=process&json=1"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        results = []
        if "products" in data:
            for p in data["products"][:5]:
                nutri = p.get("nutriments", {})
                results.append({
                    "nom": p.get("product_name", "Inconnu"),
                    "kcal": int(nutri.get("energy-kcal_100g", 0)),
                    "prot": float(nutri.get("proteins_100g", 0)),
                    "gluc": float(nutri.get("carbohydrates_100g", 0)),
                    "lip": float(nutri.get("fat_100g", 0))
                })
        return results
    except:
        return []

def create_pdf(courses_dict):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)
    pdf.cell(200, 10, txt="Ma Liste de Courses", ln=1, align='C')
    pdf.ln(10)
    for rayon, items in courses_dict.items():
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 10, rayon, ln=1)
        pdf.set_font("Arial", size=11)
        for ing, poids in items:
            pdf.cell(0, 8, f" - {ing}: {poids}g", ln=1)
        pdf.ln(5)
    return pdf.output(dest='S').encode('latin-1', 'ignore')

# --- LOGIQUE RAYONS ---
RAYONS = {
    "🥩 Boucherie": ["boeuf", "poulet", "dinde", "porc", "jambon", "lardon", "saumon", "poisson", "thon", "viande", "steak"],
    "🥦 Fruits & Légumes": ["pomme", "terre", "légume", "banane", "avocat", "citron", "salade", "tomate", "oignon", "carotte", "courgette"],
    "🥛 Crèmerie": ["crème", "beurre", "yaourt", "fromage", "lait", "oeuf", "skyr"],
    "🍝 Épicerie": ["pâtes", "riz", "pain", "farine", "sucre", "huile", "sel", "poivre", "sauce", "conserve"],
}
def detect_rayon(nom):
    for r, mots in RAYONS.items():
        if any(m in nom.lower() for m in mots): return r
    return "🛒 Divers"

# ==============================================================================
# GESTION DONNÉES
# ==============================================================================
def normalize_ingredient(val):
    if isinstance(val, (int, float)):
        return {"kcal": int(val), "prot": 0, "gluc": 0, "lip": 0}
    return val

def fetch_from_cloud(key):
    try:
        client = get_gspread_client()
        sh = client.open("ProjetPoids_DB")
        worksheet = sh.worksheet(TABS_MAPPING[key])
        raw_data = worksheet.acell('A1').value
        if not raw_data: return {}
        data = json.loads(raw_data)
        if key == "garde_manger":
            new_data = {}
            for k, v in data.items():
                new_data[k] = normalize_ingredient(v)
            return new_data
        return data
    except Exception as e:
        return {}

def push_to_cloud(key, data):
    try:
        client = get_gspread_client()
        sh = client.open("ProjetPoids_DB")
        worksheet = sh.worksheet(TABS_MAPPING[key])
        json_str = json.dumps(data, ensure_ascii=False)
        worksheet.update_acell('A1', json_str)
    except Exception as e:
        st.error(f"Erreur cloud: {e}")

DEFAULTS_PANTRY = {
    "Pâtes (Cru)": {"kcal": 360, "prot": 12, "gluc": 70, "lip": 1},
    "Riz (Cru)": {"kcal": 350, "prot": 7, "gluc": 78, "lip": 0.5},
    "Poulet": {"kcal": 110, "prot": 23, "gluc": 0, "lip": 1},
    "Oeuf": {"kcal": 140, "prot": 13, "gluc": 1, "lip": 10},
    "Huile": {"kcal": 900, "prot": 0, "gluc": 0, "lip": 100}
}

def init_state():
    keys = ["recettes", "plats", "planning", "journal", "poids", "garde_manger"]
    if "data_loaded" not in st.session_state:
        with st.spinner('Chargement...'):
            for k in keys:
                st.session_state[k] = fetch_from_cloud(k)
                time.sleep(0.2)
            if not st.session_state["garde_manger"]:
                st.session_state["garde_manger"] = DEFAULTS_PANTRY.copy()
                push_to_cloud("garde_manger", st.session_state["garde_manger"])
            st.session_state["data_loaded"] = True
    
    # Initialisation du Plateau Repas temporaire (V17)
    if "basket" not in st.session_state:
        st.session_state.basket = []

def save_data(key, new_data):
    st.session_state[key] = new_data
    push_to_cloud(key, new_data)

def get_today_str(): return datetime.now().strftime("%Y-%m-%d")

# ==============================================================================
# INTERFACE
# ==============================================================================
st.set_page_config(page_title="Le Portionneur V17", page_icon="🏗️", layout="wide")
st.title("🏗️ Le Portionneur : Plateau Repas")

init_state()

# Raccourcis
recettes = st.session_state["recettes"]
plats_vides = st.session_state["plats"]
planning = st.session_state["planning"]
journal = st.session_state["journal"]
poids_data = st.session_state["poids"]
pantry = st.session_state["garde_manger"]

MOMENTS = ["Matin", "Midi", "Collation", "Soir"]
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# --- SIDEBAR ---
with st.sidebar:
    st.header("🎯 Objectifs")
    obj_cal = st.number_input("Cible Kcal Jour", 1500, 4000, 2000, step=50)
    st.caption(f"P: {int(obj_cal*0.3/4)}g | G: {int(obj_cal*0.4/4)}g | L: {int(obj_cal*0.3/9)}g")
    
    st.write("---")
    if st.button("🔄 Synchro"):
        for k in list(st.session_state.keys()): del st.session_state[k]
        st.rerun()
    if st.button("🧹 Reset Semaine"):
        st.session_state["planning"] = {}
        st.session_state["journal"] = {}
        push_to_cloud("planning", {})
        push_to_cloud("journal", {})
        st.success("Semaine effacée !")
        st.rerun()

tabs = st.tabs(["🏠 Cockpit", "🔮 Oracle", "🛒 Courses", "🥫 Garde-Manger (IA)", "👨‍🍳 Recettes", "📅 Planning", "⚖️ Poids", "🧽 Plats"])

# --- 1. COCKPIT (AVEC PLATEAU REPAS) ---
with tabs[0]:
    # Totaux Jour
    today = get_today_str()
    if today not in journal: journal[today] = []
    tot_k = sum([e['kcal'] for e in journal[today]])
    tot_p = sum([e.get('prot', 0) for e in journal[today]])
    tot_g = sum([e.get('gluc', 0) for e in journal[today]])
    tot_l = sum([e.get('lip', 0) for e in journal[today]])
    
    st.markdown(f"### 📊 Total Jour : {int(tot_k)} / {obj_cal} kcal")
    st.progress(min(tot_k/obj_cal, 1.0))
    c_p, c_g, c_l = st.columns(3)
    c_p.caption(f"Prot: {int(tot_p)}g"); c_g.caption(f"Gluc: {int(tot_g)}g"); c_l.caption(f"Lip: {int(tot_l)}g")
    st.write("---")

    col_recette, col_separateur, col_assemblage = st.columns([10, 1, 10])
    
    # --- MODE 1 : RECETTES ---
    with col_recette:
        st.subheader("🍲 Plat Cuisiné")
        st.caption("Recette mélangeant plusieurs ingrédients")
        
        if not recettes: st.warning("Crée des recettes !")
        else:
            now = datetime.now() + timedelta(hours=1)
            jour = JOURS[now.weekday()]
            h = now.hour
            mom = "Matin" if h<11 else "Midi" if h<15 else "Collation" if h<18 else "Soir"
            
            idx, obj_repas = 0, 600
            if jour in planning and mom in planning[jour]:
                p = planning[jour][mom]
                if p["recette"] in recettes:
                    idx = sorted(list(recettes.keys())).index(p["recette"])
                    obj_repas = p["cible"]
                    st.info(f"📅 {jour} {mom} : {p['recette']}")

            ch = st.selectbox("Recette", sorted(list(recettes.keys())), index=idx, key="m_s")
            r_data = recettes[ch]
            
            c1, c2 = st.columns(2)
            with c1:
                tr = st.checkbox("Tare", True, key="m_t")
                pa = st.number_input("Poids Total Cuit", 0, step=10, key="m_p")
                pn = pa
                if tr and plats_vides:
                    pl = st.selectbox("Contenant", list(plats_vides.keys()), key="m_pl")
                    pn = max(0, pa - plats_vides[pl])
                    if pn > 0: st.caption(f"Nourriture: **{pn}g**")
            with c2: ob = st.number_input("Cible Kcal", value=obj_repas, step=50, key="m_o")
            
            if pn > 0:
                ratio = ob / r_data['total_cal']
                por = ratio * pn
                fp = r_data.get('total_prot', 0) * ratio
                fg = r_data.get('total_gluc', 0) * ratio
                fl = r_data.get('total_lip', 0) * ratio
                
                st.success(f"👉 Sers-toi : **{int(por)} g**")
                
                if st.button("✅ Valider Recette", type="primary"):
                    journal[today].append({
                        "heure": now.strftime("%H:%M"), "recette": ch, "poids": int(por), 
                        "kcal": ob, "prot": int(fp), "gluc": int(fg), "lip": int(fl)
                    })
                    save_data("journal", journal)
                    st.rerun()

    with col_separateur:
        st.markdown("<div style='border-left:1px solid #333; height:500px'></div>", unsafe_allow_html=True)

    # --- MODE 2 : PLATEAU REPAS (ASSEMBLAGE) ---
    with col_assemblage:
        st.subheader("🥪 Assemblage / Plateau")
        st.caption("Construis ton repas ingrédient par ingrédient")

        # 1. Selecteur et Ajout au Panier
        def upd_direct():
            i, w = st.session_state.d_n, st.session_state.d_p
            if i and i in pantry:
                infos = normalize_ingredient(pantry[i])
                f = w / 100
                st.session_state.d_k = int(infos['kcal'] * f)

        c_add1, c_add2 = st.columns([2, 1])
        d_ing = c_add1.selectbox("Ingrédient", [""] + sorted(list(pantry.keys())), key="d_n", on_change=upd_direct)
        d_pds = c_add2.number_input("Poids (g)", 0, step=10, key="d_p", on_change=upd_direct)
        
        # Bouton Ajouter au plateau
        if st.button("⬇️ Poser sur le plateau", key="btn_add_bsk"):
            if d_ing and d_pds > 0:
                infos = normalize_ingredient(pantry[d_ing])
                f = d_pds / 100
                st.session_state.basket.append({
                    "nom": d_ing,
                    "poids": d_pds,
                    "kcal": int(infos['kcal'] * f),
                    "prot": int(infos['prot'] * f),
                    "gluc": int(infos['gluc'] * f),
                    "lip": int(infos['lip'] * f)
                })
                # Reset inputs (via rerun ou juste state clean)
                st.session_state.d_n = ""
                st.session_state.d_p = 0
                st.rerun()

        # 2. Visualisation du Plateau
        st.write("---")
        st.markdown("#### 🍽️ Ton Plateau")
        
        bsk_tot_k = sum([x['kcal'] for x in st.session_state.basket])
        bsk_tot_p = sum([x['prot'] for x in st.session_state.basket])
        
        # Liste des items du panier
        if not st.session_state.basket:
            st.info("Plateau vide.")
        else:
            for i, item in enumerate(st.session_state.basket):
                c1, c2, c3, c4 = st.columns([4, 2, 2, 1])
                c1.text(f"{item['nom']}")
                c2.text(f"{item['poids']}g")
                c3.text(f"{item['kcal']}k")
                if c4.button("X", key=f"rm_bsk_{i}"):
                    st.session_state.basket.pop(i)
                    st.rerun()
            
            st.write("---")
            # CIBLE DU PLATEAU
            target_repas = st.number_input("🎯 Objectif pour ce repas (kcal)", value=600, step=50)
            
            # Indicateur visuel
            delta = bsk_tot_k - target_repas
            msg_delta = f"⚠️ Trop (+{delta})" if delta > 0 else f"✅ Marge (-{abs(delta)})"
            col_res1, col_res2 = st.columns(2)
            col_res1.metric("Total Plateau", f"{bsk_tot_k} kcal", delta=f"{delta} vs Objectif", delta_color="inverse")
            col_res2.metric("Protéines", f"{bsk_tot_p} g")
            
            # 3. Validation Finale
            if st.button("🍴 Tout Manger (Valider)", type="primary", use_container_width=True):
                now_h = datetime.now().strftime("%H:%M")
                for item in st.session_state.basket:
                    journal[today].append({
                        "heure": now_h,
                        "recette": f"🔹 {item['nom']}",
                        "poids": item['poids'],
                        "kcal": item['kcal'],
                        "prot": item['prot'],
                        "gluc": item['gluc'],
                        "lip": item['lip']
                    })
                save_data("journal", journal)
                st.session_state.basket = [] # Vider le panier
                st.success("Repas validé !")
                time.sleep(1)
                st.rerun()


# --- 2. L'ORACLE ---
with tabs[1]:
    st.header("🔮 L'Oracle")
    if len(poids_data) < 2: st.warning("Il me faut au moins 2 pesées.")
    else:
        dates = sorted(poids_data.keys())
        p1, p2 = poids_data[dates[0]], poids_data[dates[-1]]
        days = (datetime.strptime(dates[-1], "%Y-%m-%d") - datetime.strptime(dates[0], "%Y-%m-%d")).days
        if days > 0:
            vitesse = (p1 - p2) / days
            c1, c2 = st.columns(2)
            c1.metric("Vitesse", f"{vitesse*7:.2f} kg/semaine")
            if vitesse > 0:
                obj_poids = c2.number_input("Objectif", value=p2-5)
                jours = int((p2 - obj_poids) / vitesse)
                st.success(f"Objectif atteint dans {jours} jours !")

# --- 3. COURSES ---
with tabs[2]:
    st.header("🛒 Courses")
    if st.button("Générer PDF"):
        sh = {}
        for j in planning:
            for m in planning[j]:
                r = planning[j][m]["recette"]
                if r in recettes:
                    for i in recettes[r]["ingredients"]: sh[i["nom"]] = sh.get(i["nom"], 0) + i["poids"]
        sh_tri = {}
        for ing, poids in sh.items():
            r = detect_rayon(ing)
            if r not in sh_tri: sh_tri[r] = []
            sh_tri[r].append((ing, poids))
        pdf_bytes = create_pdf(sh_tri)
        st.download_button("⬇️ PDF", data=pdf_bytes, file_name="courses.pdf", mime='application/pdf')

# --- 4. GARDE MANGER ---
with tabs[3]:
    st.header("🥫 Ingrédients (IA)")
    with st.expander("🔎 Rechercher (OpenFoodFacts)", expanded=True):
        query = st.text_input("Recherche (ex: Avocat)")
        if query:
            res = search_openfoodfacts(query)
            for r in res:
                c1, c2, c3 = st.columns([3, 2, 1])
                c1.write(f"**{r['nom']}**")
                c1.caption(f"{r['kcal']} kcal")
                if c3.button("Ajouter", key=f"off_{r['nom']}"):
                    pantry[r['nom']] = {"kcal": r['kcal'], "prot": r['prot'], "gluc": r['gluc'], "lip": r['lip']}
                    save_data("garde_manger", pantry)
                    st.rerun()

    st.write("---")
    search_gm = st.text_input("Filtrer")
    items = pantry.items()
    if search_gm: items = {k:v for k,v in pantry.items() if search_gm.lower() in k.lower()}.items()
    for k, v in sorted(items):
        vals = normalize_ingredient(v)
        with st.expander(f"{k} ({vals['kcal']} kcal)"):
            c1, c2, c3, c4, c5 = st.columns(5)
            nk = c1.number_input("K", value=vals['kcal'], key=f"k_{k}")
            np = c2.number_input("P", value=float(vals['prot']), key=f"p_{k}")
            ng = c3.number_input("G", value=float(vals['gluc']), key=f"g_{k}")
            nl = c4.number_input("L", value=float(vals['lip']), key=f"l_{k}")
            if c5.button("💾", key=f"sv_{k}"):
                pantry[k] = {"kcal": nk, "prot": np, "gluc": ng, "lip": nl}
                save_data("garde_manger", pantry); st.rerun()
            if c5.button("🗑️", key=f"dl_{k}"): del pantry[k]; save_data("garde_manger", pantry); st.rerun()

# --- 5. RECETTES ---
with tabs[4]:
    st.header("👨‍🍳 Recettes")
    ls = sorted(list(recettes.keys()))
    cg, cd = st.columns([1, 2])
    with cg:
        md = st.radio("Mode", ["Nouvelle", "Modifier", "Dupliquer", "Supprimer"], key="rm")
        tg = None if md=="Nouvelle" else st.selectbox("Recette", ls, key="rt")
        if md=="Supprimer" and st.button("Confirmer"): del recettes[tg]; save_data("recettes", recettes); st.rerun()

    with cd:
        if md != "Supprimer":
            if 'ti' not in st.session_state or st.session_state.get('lm') != md or st.session_state.get('lt') != tg:
                st.session_state.ti = []
                if (md in ["Modifier", "Dupliquer"]) and tg: st.session_state.ti = recettes[tg]["ingredients"].copy()
                st.session_state.lm = md; st.session_state.lt = tg
            
            dn = tg if md == "Modifier" and tg else f"{tg} (Copie)" if md=="Dupliquer" and tg else ""
            rn = st.text_input("Nom", value=dn, disabled=(md=="Modifier"), key="rn")
            
            def upd():
                i, w = st.session_state.ra_n, st.session_state.ra_p
                if i and i in pantry:
                    infos = normalize_ingredient(pantry[i])
                    f = w / 100
                    st.session_state.ra_k = int(infos['kcal'] * f)

            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            ni = c1.selectbox("Ajout", [""] + sorted(list(pantry.keys())), key="ra_n", on_change=upd)
            np = c2.number_input("g", 0, step=10, key="ra_p", on_change=upd)
            nk = c3.number_input("kcal", 0, key="ra_k")
            if c4.button("➕") and ni:
                infos = normalize_ingredient(pantry[ni])
                f = np / 100
                st.session_state.ti.append({"nom": ni, "poids": np, "cal": nk, "prot": infos['prot']*f, "gluc": infos['gluc']*f, "lip": infos['lip']*f})
            
            # TABLEAU SECURISE
            if st.session_state.ti:
                st.table(pd.DataFrame(st.session_state.ti)[['nom', 'poids', 'cal']])
            else:
                st.info("Aucun ingrédient.")

            if st.button("💾 Sauver") and rn:
                recettes[rn] = {"total_cal": sum([x['cal'] for x in st.session_state.ti]), "total_prot": sum([x['prot'] for x in st.session_state.ti]), "ingredients": st.session_state.ti}
                save_data("recettes", recettes); st.rerun()

# --- 6. PLANNING & 7. POIDS/PLATS ---
with tabs[5]:
    lr = ["(Rien)"] + sorted(list(recettes.keys()))
    if not planning: planning = {}
    with st.form("pf"):
        for j in JOURS:
            if j not in planning: planning[j] = {}
            with st.expander(j):
                cs = st.columns(4)
                for i, m in enumerate(MOMENTS):
                    curr = planning[j].get(m, {})
                    idx = lr.index(curr.get("recette", "(Rien)")) if curr.get("recette") in lr else 0
                    nr = cs[i].selectbox(m, lr, index=idx, key=f"p_{j}_{m}")
                    nc = 0
                    if nr != "(Rien)": nc = cs[i].number_input("k", value=curr.get("cible", 600), step=50, key=f"pc_{j}_{m}")
                    st.session_state[f"st_{j}_{m}"] = {"recette": nr, "cible": nc}
        if st.form_submit_button("Sauver"):
            np = {}
            for j in JOURS:
                np[j] = {}
                for m in MOMENTS:
                    if st.session_state[f"st_{j}_{m}"]["recette"] != "(Rien)": np[j][m] = st.session_state[f"st_{j}_{m}"]
            save_data("planning", np); st.rerun()

with tabs[6]:
    w = st.number_input("Kg", 0.0, key="wp")
    if st.button("S") and w>0: poids_data[today]=w; save_data("poids", poids_data); st.rerun()
    if poids_data: st.line_chart(pd.DataFrame({"Date": sorted(poids_data.keys()), "Poids": [poids_data[d] for d in sorted(poids_data.keys())]}).set_index("Date"))

with tabs[7]:
    pn = st.text_input("Nom"); pw = st.number_input("Poids", 0)
    if st.button("Ajouter") and pn: plats_vides[pn]=pw; save_data("plats", plats_vides); st.rerun()
    for k,v in plats_vides.items():
        st.write(f"{k}: {v}"); 
        if st.button("X", key=f"pd_{k}"): del plats_vides[k]; save_data("plats", plats_vides); st.rerun()
