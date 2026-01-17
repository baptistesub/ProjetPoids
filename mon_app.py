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

# --- OUTILS EXTERNES (OPENFOODFACTS & PDF) ---
def search_openfoodfacts(query):
    """Cherche un produit sur la base mondiale"""
    url = f"https://world.openfoodfacts.org/cgi/search.pl?search_terms={query}&search_simple=1&action=process&json=1"
    try:
        r = requests.get(url, timeout=5)
        data = r.json()
        results = []
        if "products" in data:
            for p in data["products"][:5]: # Top 5 résultats
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
# GESTION DONNÉES & MIGRATION MACROS
# ==============================================================================
def normalize_ingredient(val):
    """Transforme un simple chiffre (V14) en dictionnaire macros (V15)"""
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
        
        # MIGRATION AUTOMATIQUE V15
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

# Données par défaut (Format V15)
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
        with st.spinner('Mise à jour vers V15 (Macros & IA)...'):
            for k in keys:
                st.session_state[k] = fetch_from_cloud(k)
                time.sleep(0.2)
            # Init Garde Manger si vide
            if not st.session_state["garde_manger"]:
                st.session_state["garde_manger"] = DEFAULTS_PANTRY.copy()
                push_to_cloud("garde_manger", st.session_state["garde_manger"])
            st.session_state["data_loaded"] = True

def save_data(key, new_data):
    st.session_state[key] = new_data
    push_to_cloud(key, new_data)

def get_today_str(): return datetime.now().strftime("%Y-%m-%d")

# ==============================================================================
# INTERFACE
# ==============================================================================
st.set_page_config(page_title="Coach Ultimate V15", page_icon="🦁", layout="wide")
st.title("🦁 Le Portionneur : Ultimate")

init_state()

# Raccourcis State
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
    st.header("🎯 Objectifs Macros")
    obj_cal = st.number_input("Cible Kcal", 1500, 4000, 2000, step=50)
    # Répartition classique 30% P / 40% G / 30% L
    c1, c2, c3 = st.columns(3)
    obj_prot = c1.number_input("Prot (g)", value=int(obj_cal*0.3/4))
    obj_gluc = c2.number_input("Gluc (g)", value=int(obj_cal*0.4/4))
    obj_lip = c3.number_input("Lip (g)", value=int(obj_cal*0.3/9))
    
    st.write("---")
    if st.button("🔄 Synchro"):
        for k in st.session_state.keys(): del st.session_state[k]
        st.rerun()
    if st.button("🧹 Reset Semaine"):
        st.session_state["planning"] = {}
        st.session_state["journal"] = {}
        push_to_cloud("planning", {})
        push_to_cloud("journal", {})
        st.success("Semaine effacée !")
        st.rerun()

tabs = st.tabs(["🏠 Cockpit", "🔮 Oracle", "🛒 Courses", "🥫 Garde-Manger (IA)", "👨‍🍳 Recettes", "📅 Planning", "⚖️ Poids", "🧽 Plats"])

# --- 1. COCKPIT (AVEC MACROS) ---
with tabs[0]:
    col_main, col_trk = st.columns([2, 1])
    today = get_today_str()
    if today not in journal: journal[today] = []
    
    # Calcul Totaux Jour
    tot_k = sum([e['kcal'] for e in journal[today]])
    tot_p = sum([e.get('prot', 0) for e in journal[today]])
    tot_g = sum([e.get('gluc', 0) for e in journal[today]])
    tot_l = sum([e.get('lip', 0) for e in journal[today]])
    
    with col_trk:
        st.markdown("### 📊 Aujourd'hui")
        st.metric("Kcal", f"{int(tot_k)} / {obj_cal}")
        st.progress(min(tot_k/obj_cal, 1.0))
        
        c1, c2, c3 = st.columns(3)
        c1.metric("Prot", f"{int(tot_p)}", delta=f"{int(tot_p-obj_prot)}")
        c2.metric("Gluc", f"{int(tot_g)}", delta=f"{int(tot_g-obj_gluc)}")
        c3.metric("Lip", f"{int(tot_l)}", delta=f"{int(tot_l-obj_lip)}")
        
        with st.expander("⚡ Express"):
            xn = st.text_input("Quoi", key="xn")
            xk = st.number_input("Kcal", 0, key="xk")
            xp = st.number_input("Prot", 0, key="xp")
            if st.button("Add", key="xb") and xn:
                journal[today].append({"heure": datetime.now().strftime("%H:%M"), "recette": f"⚡ {xn}", "poids": 0, "kcal": xk, "prot": xp, "gluc": 0, "lip": 0})
                save_data("journal", journal)
                st.rerun()

    with col_main:
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

            ch = st.selectbox("Plat", sorted(list(recettes.keys())), index=idx, key="m_s")
            # Recup Macros Recette
            r_data = recettes[ch]
            ct = r_data['total_cal']
            
            # Si anciennes recettes (V14), on met 0 en macros
            cp = r_data.get('total_prot', 0)
            
            c1, c2 = st.columns(2)
            with c1:
                tr = st.checkbox("Tare", True, key="m_t")
                pa = st.number_input("Poids Balance", 1000, step=10, key="m_p")
                pn = pa
                if tr and plats_vides:
                    pl = st.selectbox("Contenant", list(plats_vides.keys()), key="m_pl")
                    pn = max(0, pa - plats_vides[pl])
                    st.caption(f"Net: **{pn}g**")
            with c2: ob = st.number_input("Objectif Kcal", value=obj_repas, step=50, key="m_o")
            
            if pn > 0:
                ratio = ob / ct
                por = ratio * pn
                final_p = cp * ratio
                final_g = r_data.get('total_gluc', 0) * ratio
                final_l = r_data.get('total_lip', 0) * ratio
                
                st.success(f"👉 Portion : **{int(por)} g**")
                st.caption(f"Apport : {int(ob)} kcal | {int(final_p)}g Prot | {int(final_g)}g Gluc | {int(final_l)}g Lip")
                
                if st.button("✅ Manger", type="primary"):
                    journal[today].append({
                        "heure": now.strftime("%H:%M"), 
                        "recette": ch, 
                        "poids": int(por), 
                        "kcal": ob,
                        "prot": int(final_p),
                        "gluc": int(final_g),
                        "lip": int(final_l)
                    })
                    save_data("journal", journal)
                    st.rerun()

# --- 2. L'ORACLE (PREDICTION) ---
with tabs[1]:
    st.header("🔮 L'Oracle")
    if len(poids_data) < 2:
        st.warning("Il me faut au moins 2 pesées à des dates différentes pour prédire l'avenir.")
    else:
        # Calcul basique de tendance
        dates = sorted(poids_data.keys())
        p1, p2 = poids_data[dates[0]], poids_data[dates[-1]]
        d1 = datetime.strptime(dates[0], "%Y-%m-%d")
        d2 = datetime.strptime(dates[-1], "%Y-%m-%d")
        days = (d2 - d1).days
        if days > 0:
            perte_totale = p1 - p2
            vitesse = perte_totale / days # kg par jour
            
            c1, c2 = st.columns(2)
            c1.metric("Vitesse actuelle", f"{vitesse*7:.2f} kg/semaine")
            
            if vitesse > 0:
                obj_poids = c2.number_input("Objectif Poids (kg)", value=p2-5)
                kg_restants = p2 - obj_poids
                jours_restants = int(kg_restants / vitesse)
                date_fin = d2 + timedelta(days=jours_restants)
                st.success(f"🎉 Tu atteindras {obj_poids} kg le **{date_fin.strftime('%d/%m/%Y')}** (dans {jours_restants} jours) si tu continues comme ça.")
            else:
                st.error("Attention, la tendance est à la hausse ou stable.")

# --- 3. COURSES (PDF) ---
with tabs[2]:
    st.header("🛒 Courses")
    if st.button("Générer PDF"):
        sh = {}
        for j in planning:
            for m in planning[j]:
                r = planning[j][m]["recette"]
                if r in recettes:
                    for i in recettes[r]["ingredients"]:
                        sh[i["nom"]] = sh.get(i["nom"], 0) + i["poids"]
        
        sh_tri = {}
        for ing, poids in sh.items():
            r = detect_rayon(ing)
            if r not in sh_tri: sh_tri[r] = []
            sh_tri[r].append((ing, poids))
            
        pdf_bytes = create_pdf(sh_tri)
        st.download_button("⬇️ Télécharger la liste (PDF)", data=pdf_bytes, file_name="courses.pdf", mime='application/pdf')
        
        # Affichage écran
        cols = st.columns(2)
        idx=0
        for ray, lst in sh_tri.items():
            with cols[idx%2]:
                st.subheader(ray)
                for i, p in lst: st.write(f"- {i}: {p}g")
            idx+=1

# --- 4. GARDE MANGER (OPENFOODFACTS) ---
with tabs[3]:
    st.header("🥫 Ingrédients & IA")
    
    with st.expander("🔎 Rechercher un produit (OpenFoodFacts)", expanded=True):
        query = st.text_input("Code barre ou nom (ex: Barilla)")
        if query:
            res = search_openfoodfacts(query)
            if not res: st.warning("Rien trouvé.")
            for r in res:
                c1, c2, c3 = st.columns([3, 2, 1])
                c1.write(f"**{r['nom']}**")
                c1.caption(f"{r['kcal']} kcal | P:{r['prot']} G:{r['gluc']} L:{r['lip']}")
                if c3.button("Ajouter", key=f"off_{r['nom']}"):
                    pantry[r['nom']] = {"kcal": r['kcal'], "prot": r['prot'], "gluc": r['gluc'], "lip": r['lip']}
                    save_data("garde_manger", pantry)
                    st.success("Ajouté !")
                    st.rerun()

    st.write("---")
    st.subheader("Mes produits")
    # Gestion de l'affichage avec macros éditables
    search_gm = st.text_input("Filtrer mes produits")
    items = pantry.items()
    if search_gm: items = {k:v for k,v in pantry.items() if search_gm.lower() in k.lower()}.items()
    
    for k, v in sorted(items):
        # v est maintenant un dictionnaire {kcal, prot...} ou un int (si vieille version)
        vals = normalize_ingredient(v)
        
        with st.expander(f"{k} ({vals['kcal']} kcal)"):
            c1, c2, c3, c4, c5 = st.columns(5)
            nk = c1.number_input("Kcal", value=vals['kcal'], key=f"k_{k}")
            np = c2.number_input("Prot", value=float(vals['prot']), key=f"p_{k}")
            ng = c3.number_input("Gluc", value=float(vals['gluc']), key=f"g_{k}")
            nl = c4.number_input("Lip", value=float(vals['lip']), key=f"l_{k}")
            
            if c5.button("Sauver", key=f"sv_{k}"):
                pantry[k] = {"kcal": nk, "prot": np, "gluc": ng, "lip": nl}
                save_data("garde_manger", pantry)
                st.rerun()
            if c5.button("Suppr", key=f"dl_{k}"):
                del pantry[k]
                save_data("garde_manger", pantry)
                st.rerun()

# --- 5. RECETTES (AVEC MACROS) ---
with tabs[4]:
    st.header("👨‍🍳 Recettes")
    ls = sorted(list(recettes.keys()))
    
    cg, cd = st.columns([1, 2])
    with cg:
        md = st.radio("Mode", ["Nouvelle", "Modifier", "Dupliquer", "Supprimer"], key="rm")
        tg = None if md=="Nouvelle" else st.selectbox("Recette", ls, key="rt")
        if md=="Supprimer" and st.button("Confirmer"):
            del recettes[tg]; save_data("recettes", recettes); st.rerun()

    with cd:
        if md != "Supprimer":
            if 'ti' not in st.session_state or st.session_state.get('lm') != md or st.session_state.get('lt') != tg:
                st.session_state.ti = []
                if (md in ["Modifier", "Dupliquer"]) and tg: st.session_state.ti = recettes[tg]["ingredients"].copy()
                st.session_state.lm = md; st.session_state.lt = tg
            
            dn = tg if md == "Modifier" and tg else f"{tg} (Copie)" if md=="Dupliquer" and tg else ""
            rn = st.text_input("Nom", value=dn, disabled=(md=="Modifier"), key="rn")
            
            # Update auto
            def upd():
                i, w = st.session_state.ra_n, st.session_state.ra_p
                if i and i in pantry:
                    infos = normalize_ingredient(pantry[i])
                    factor = w / 100
                    st.session_state.ra_k = int(infos['kcal'] * factor)
                    st.session_state.ra_pr = infos['prot'] * factor
                    st.session_state.ra_gl = infos['gluc'] * factor
                    st.session_state.ra_li = infos['lip'] * factor

            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            ni = c1.selectbox("Ingrédient", [""] + sorted(list(pantry.keys())), key="ra_n", on_change=upd)
            np = c2.number_input("Poids (g)", 0, step=10, key="ra_p", on_change=upd)
            nk = c3.number_input("Kcal", 0, key="ra_k")
            
            # Champs cachés pour macros (calculés mais modifiables si on veut afficher)
            # On stocke juste dans le bouton ajout
            
            if c4.button("➕") and ni:
                # Recup macros courantes
                infos = normalize_ingredient(pantry[ni])
                factor = np / 100
                st.session_state.ti.append({
                    "nom": ni, "poids": np, "cal": nk,
                    "prot": infos['prot']*factor, "gluc": infos['gluc']*factor, "lip": infos['lip']*factor
                })
            
            st.write("---")
            tot_k, tot_p, tot_g, tot_l = 0,0,0,0
            rdl = -1
            for i, x in enumerate(st.session_state.ti):
                c1, c2, c3, c4 = st.columns([3, 1, 2, 1])
                c1.text(x['nom'])
                c2.text(f"{x['poids']}g")
                # Gestion compatibilité vieux ingrédients sans macros
                xp, xg, xl = x.get('prot',0), x.get('gluc',0), x.get('lip',0)
                c3.caption(f"{x['cal']}k | P:{int(xp)} G:{int(xg)} L:{int(xl)}")
                tot_k+=x['cal']; tot_p+=xp; tot_g+=xg; tot_l+=xl
                if c4.button("🗑️", key=f"d{i}"): rdl=i
            
            if rdl>=0: st.session_state.ti.pop(rdl); st.rerun()
            
            st.info(f"Total: {tot_k} kcal | P: {int(tot_p)}g | G: {int(tot_g)}g | L: {int(tot_l)}g")
            
            if st.button("💾 Sauver", type="primary") and rn:
                recettes[rn] = {
                    "total_cal": tot_k, "total_prot": tot_p, "total_gluc": tot_g, "total_lip": tot_l,
                    "ingredients": st.session_state.ti
                }
                save_data("recettes", recettes)
                st.success("Sauvé"); st.session_state.ti=[]; st.rerun()

# --- 6. PLANNING ---
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
                    nr = cs[i].selectbox(m, lr, index=idx, key=f"p_{j}_{m}", label_visibility="collapsed")
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

# --- 7. POIDS ---
with tabs[6]:
    w = st.number_input("Kg", 0.0, 200.0, step=0.1, format="%.1f", key="wp")
    if st.button("Sauver", key="ws") and w>0: poids_data[today]=w; save_data("poids", poids_data); st.rerun()
    if poids_data:
        dts = sorted(poids_data.keys())
        st.line_chart(pd.DataFrame({"Date": dts, "Poids": [poids_data[d] for d in dts]}).set_index("Date"))

# --- 8. PLATS ---
with tabs[7]:
    pn = st.text_input("Nom", key="pn"); pw = st.number_input("Poids", 0, key="pw")
    if st.button("Ajouter", key="pa") and pn: plats_vides[pn]=pw; save_data("plats", plats_vides); st.rerun()
    for k,v in plats_vides.items():
        c1, c2, c3 = st.columns([3, 2, 1]); c1.text(k); c2.text(v)
        if c3.button("X", key=f"pd_{k}"): del plats_vides[k]; save_data("plats", plats_vides); st.rerun()
