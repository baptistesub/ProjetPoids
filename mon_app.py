import streamlit as st
import json
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ==============================================================================
# 1. CONNEXION CLOUD (GOOGLE SHEETS)
# ==============================================================================
# On récupère les secrets depuis Streamlit Cloud
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

# Fonction de connexion avec cache pour ne pas reconnecter à chaque clic
@st.cache_resource
def get_gspread_client():
    # On reconstruit le dictionnaire de clés depuis les secrets Streamlit
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    return client

def get_sheet():
    client = get_gspread_client()
    # Ouvre le fichier Google Sheet par son nom
    return client.open("ProjetPoids_DB")

# Noms des onglets dans le Google Sheet
TABS_MAPPING = {
    "recettes": "recettes",
    "plats": "plats",
    "planning": "planning",
    "garde_manger": "garde_manger",
    "journal": "journal",
    "poids": "poids"
}

# ==============================================================================
# 2. GESTION DES DONNÉES (LECTURE / ÉCRITURE DANS CELLULE A1)
# ==============================================================================
# Astuce : On stocke tout le JSON dans la cellule A1 de chaque onglet.
# C'est simple, robuste et gratuit.

def load_data(key):
    try:
        sh = get_sheet()
        worksheet = sh.worksheet(TABS_MAPPING[key])
        # On lit la cellule A1
        raw_data = worksheet.acell('A1').value
        if not raw_data:
            return {}
        return json.loads(raw_data)
    except Exception as e:
        # Si l'onglet est vide ou erreur, on retourne vide
        return {}

def save_data(key, data):
    try:
        sh = get_sheet()
        worksheet = sh.worksheet(TABS_MAPPING[key])
        # On transforme le dict en texte JSON et on l'écrit en A1
        json_str = json.dumps(data, ensure_ascii=False)
        worksheet.update_acell('A1', json_str)
    except Exception as e:
        st.error(f"Erreur de sauvegarde Cloud : {e}")

# --- Données par défaut (pour initialiser si vide) ---
DEFAULTS_PANTRY = {
    "Pâtes / Riz (Cru)": 360, "Pâtes / Riz (Cuit)": 130,
    "Pomme de terre": 80, "Légumes (Moyenne)": 40, "Pain": 260, "Farine": 360, "Sucre": 400,
    "Boeuf 5%": 125, "Boeuf 15%": 250, "Poulet (Filet)": 110,
    "Oeuf (unité)": 70, "Lardons": 300, "Jambon blanc": 110, "Poisson blanc": 80, "Saumon": 200,
    "Crème fraiche 15%": 160, "Crème fraiche 30%": 300,
    "Huile / Beurre": 900, "Sauce Tomate": 40, "Fromage râpé": 380, "Yaourt Nature": 50,
    "Lait Demi-Ecrémé": 46, "Avocat": 160, "Banane": 89, "Pomme": 52
}

def get_full_pantry():
    custom = load_data("garde_manger")
    if not custom:
        custom = DEFAULTS_PANTRY.copy()
        save_data("garde_manger", custom)
    return custom

def get_today_str():
    return datetime.now().strftime("%Y-%m-%d")

# ==============================================================================
# 3. INTERFACE (IDENTIQUE A LA V11, MAIS CONNECTÉE)
# ==============================================================================
st.set_page_config(page_title="Le Portionneur Cloud", page_icon="☁️", layout="wide")
st.title("☁️ Le Portionneur : En Ligne")

# Chargement des données (Depuis Google Sheets cette fois !)
with st.spinner('Connexion à la base de données...'):
    recettes = load_data("recettes")
    plats_vides = load_data("plats")
    planning = load_data("planning")
    journal = load_data("journal")
    poids_data = load_data("poids")
    pantry = get_full_pantry()

MOMENTS = ["Matin", "Midi", "Collation", "Soir"]
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

tabs = st.tabs(["🏠 Cockpit", "⚖️ Mon Poids", "📊 Historique", "🛒 Courses", "📅 Planning", "👨‍🍳 Recettes", "🥫 Garde-Manger", "🧽 Plats"])

# --- COCKPIT ---
with tabs[0]:
    col_main, col_tracker = st.columns([2, 1])
    today = get_today_str()
    if today not in journal: journal[today] = []
    total_mange = sum([e['kcal'] for e in journal[today]])
    
    with col_tracker:
        st.markdown("### 📅 Aujourd'hui")
        st.metric("Total Kcal", f"{int(total_mange)}")
        st.progress(min(total_mange / 2200, 1.0))
        with st.expander("⚡ Ajout Express"):
            en = st.text_input("Quoi ?", key="xp_n")
            ek = st.number_input("Kcal", 0, step=50, key="xp_k")
            if st.button("Ajouter", key="xp_b") and en and ek:
                journal[today].append({"heure": datetime.now().strftime("%H:%M"), "recette": f"⚡ {en}", "poids": 0, "kcal": ek})
                save_data("journal", journal)
                st.success("Ok")
                st.rerun()

    with col_main:
        st.subheader("🍽️ Manger")
        if not recettes:
            st.warning("Aucune recette. Va dans l'onglet 'Recettes' !")
        else:
            # Auto-detect simplifié
            now = datetime.now()
            # Note: L'heure du serveur Cloud est UTC, on ajoute 1h ou 2h pour la France sommairement
            now_fr = now + timedelta(hours=1) 
            jour = JOURS[now_fr.weekday()]
            h = now_fr.hour
            mom = "Matin" if h<11 else "Midi" if h<15 else "Collation" if h<18 else "Soir"
            
            idx, obj, msg = 0, 600, None
            if jour in planning and mom in planning[jour]:
                p = planning[jour][mom]
                if p["recette"] in recettes:
                    idx = sorted(list(recettes.keys())).index(p["recette"])
                    obj = p["cible"]
                    msg = f"📅 {jour} {mom} : **{p['recette']}**"
            if msg: st.info(msg)

            choix = st.selectbox("Plat", sorted(list(recettes.keys())), index=idx, key="m_sel")
            cal_tot = recettes[choix]['total_cal']
            st.write("---")
            c1, c2 = st.columns(2)
            with c1:
                tare = st.checkbox("Tare", value=True, key="m_tare")
                p_aff = st.number_input("Poids Balance", 1000, step=10, key="m_pb")
                p_net = p_aff
                if tare and plats_vides:
                    pl = st.selectbox("Contenant", list(plats_vides.keys()), key="m_pl")
                    p_net = max(0, p_aff - plats_vides[pl])
                    st.caption(f"Nourriture : **{p_net}g**")
            with c2:
                o = st.number_input("Objectif", value=obj, step=50, key="m_obj")
            
            if p_net > 0:
                portion = (o / cal_tot) * p_net
                st.success(f"👉 Portion : **{int(portion)} g**")
                if st.button("✅ Valider", type="primary", key="m_val"):
                    journal[today].append({"heure": now_fr.strftime("%H:%M"), "recette": choix, "poids": int(portion), "kcal": o})
                    save_data("journal", journal)
                    st.rerun()

# --- POIDS ---
with tabs[1]:
    st.header("⚖️ Poids")
    w = st.number_input("Kg", 0.0, 200.0, step=0.1, format="%.1f", key="w_in")
    if st.button("Sauver", key="w_s") and w>0:
        poids_data[today] = w
        save_data("poids", poids_data)
        st.success("Sauvé")
        st.rerun()
    if poids_data:
        dates = sorted(poids_data.keys())
        st.line_chart(pd.DataFrame({"Date": dates, "Poids": [poids_data[d] for d in dates]}).set_index("Date"))

# --- HISTO ---
with tabs[2]:
    st.header("📊 Histo")
    if journal[today]:
        for i in journal[today]: st.write(f"{i['heure']} | {i['recette']} | {i['kcal']} kcal")
        if st.button("Clear Today", key="h_clr"):
            journal[today] = []
            save_data("journal", journal)
            st.rerun()
    st.write("---")
    dg = {}
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        dg[d] = sum([x['kcal'] for x in journal.get(d, [])])
    st.bar_chart(dg)

# --- COURSES ---
with tabs[3]:
    st.header("🛒 Courses")
    if st.button("Générer", key="c_gen"):
        sh = {}
        for j in planning:
            for m in planning[j]:
                r = planning[j][m]["recette"]
                if r in recettes:
                    for i in recettes[r]["ingredients"]: sh[i["nom"]] = sh.get(i["nom"], 0) + i["poids"]
        for k,v in sh.items(): st.write(f"- {k}: {v}g")

# --- PLANNING ---
with tabs[4]:
    st.header("📅 Planning")
    lr = ["(Rien)"] + sorted(list(recettes.keys()))
    if not planning: planning = {}
    with st.form("pfl"):
        for j in JOURS:
            if j not in planning: planning[j] = {}
            with st.expander(j):
                cols = st.columns(4)
                for i, m in enumerate(MOMENTS):
                    curr = planning[j].get(m, {})
                    idx = lr.index(curr.get("recette", "(Rien)")) if curr.get("recette") in lr else 0
                    nr = cols[i].selectbox(m, lr, index=idx, key=f"p_{j}_{m}", label_visibility="collapsed")
                    nc = 0
                    if nr != "(Rien)": nc = cols[i].number_input("k", value=curr.get("cible", 600), step=50, key=f"pc_{j}_{m}")
                    st.session_state[f"ps_{j}_{m}"] = {"recette": nr, "cible": nc}
        if st.form_submit_button("Sauver"):
            np = {}
            for j in JOURS:
                np[j] = {}
                for m in MOMENTS:
                    if st.session_state[f"ps_{j}_{m}"]["recette"] != "(Rien)": np[j][m] = st.session_state[f"ps_{j}_{m}"]
            save_data("planning", np)
            st.rerun()

# --- RECETTES ---
with tabs[5]:
    st.header("👨‍🍳 Recettes")
    sr = st.text_input("🔍", key="r_s")
    lshow = sorted(list(recettes.keys()))
    if sr: lshow = [x for x in lshow if sr.lower() in x.lower()]
    with st.expander("Liste"): st.table([{"Nom": x, "Kcal": recettes[x]['total_cal']} for x in lshow])
    
    c_g, c_d = st.columns([1, 2])
    with c_g:
        mod = st.radio("Action", ["Nouvelle", "Modifier", "Dupliquer", "Supprimer"], key="r_act")
        tgt = None
        if mod != "Nouvelle":
            tgt = st.selectbox("Cible", lshow, key="r_tgt")
            if mod == "Supprimer" and st.button("Confirmer", key="r_del"):
                del recettes[tgt]
                save_data("recettes", recettes)
                st.rerun()
    with c_d:
        if mod != "Supprimer":
            if 'ti' not in st.session_state or st.session_state.get('lm') != mod or st.session_state.get('lt') != tgt:
                st.session_state.ti = []
                if (mod == "Modifier" or mod == "Dupliquer") and tgt: st.session_state.ti = recettes[tgt]["ingredients"].copy()
                st.session_state.lm = mod
                st.session_state.lt = tgt
            
            dname = tgt if mod == "Modifier" and tgt else f"{tgt} (Copie)" if mod=="Dupliquer" and tgt else ""
            rname = st.text_input("Nom", value=dname, disabled=(mod=="Modifier"), key="r_nm")
            
            st.markdown("##### Ingrédients")
            ca, cb, cc, cd = st.columns([3, 2, 2, 1])
            ni = ca.selectbox("Ajout", [""] + sorted(list(pantry.keys())), key="r_add_n")
            np = cb.number_input("g", 0, step=10, key="r_add_p")
            nc = cc.number_input("kcal", value=int((np*pantry[ni])/100) if ni and ni in pantry else 0, key="r_add_k")
            if cd.button("➕", key="r_add_b") and ni: st.session_state.ti.append({"nom": ni, "poids": np, "cal": nc})
            
            st.write("---")
            rdel = -1
            tot = 0
            for i, x in enumerate(st.session_state.ti):
                c1, c2, c3, c4 = st.columns([4, 2, 2, 1])
                c1.text(x['nom'])
                c2.text(f"{x['poids']}g")
                c3.text(f"{x['cal']}kcal")
                tot += x['cal']
                if c4.button("🗑️", key=f"r_rm_{i}"): rdel = i
            if rdel >= 0:
                st.session_state.ti.pop(rdel)
                st.rerun()
            st.info(f"Total: {tot}")
            if st.button("💾 Sauver Recette", type="primary", key="r_sav") and rname:
                recettes[rname] = {"total_cal": tot, "ingredients": st.session_state.ti}
                save_data("recettes", recettes)
                st.success("Sauvé")
                if mod in ["Nouvelle", "Dupliquer"]:
                    st.session_state.ti = []
                    st.rerun()

# --- GARDE MANGER ---
with tabs[6]:
    st.header("🥫 Ingrédients")
    with st.expander("Ajout"):
        cn = st.text_input("Nom", key="gm_n")
        ck = st.number_input("Kcal/100g", 0, key="gm_k")
        if st.button("Ajouter", key="gm_a") and cn:
            pantry[cn] = ck
            save_data("garde_manger", pantry)
            st.rerun()
    search = st.text_input("Recherche", key="gm_s")
    items = pantry.items()
    if search: items = {k:v for k,v in pantry.items() if search.lower() in k.lower()}.items()
    for k, v in sorted(items):
        c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
        nn = c1.text_input(f"n{k}", k, label_visibility="collapsed", key=f"gmn_{k}")
        nk = c2.number_input(f"v{k}", value=v, label_visibility="collapsed", key=f"gmk_{k}")
        if nn != k or nk != v:
            if c3.button("💾", key=f"gms_{k}"):
                if nn != k: del pantry[k]
                pantry[nn] = nk
                save_data("garde_manger", pantry)
                st.rerun()
        if c4.button("🗑️", key=f"gmd_{k}"):
            del pantry[k]
            save_data("garde_manger", pantry)
            st.rerun()

# --- PLATS ---
with tabs[7]:
    st.header("🧽 Plats")
    pn = st.text_input("Nom", key="pl_n")
    pw = st.number_input("Poids", 0, key="pl_w")
    if st.button("Ajouter", key="pl_a") and pn:
        plats_vides[pn] = pw
        save_data("plats", plats_vides)
        st.rerun()
    for k,v in plats_vides.items():
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.text(k)
        c2.text(v)
        if c3.button("X", key=f"pld_{k}"):
            del plats_vides[k]
            save_data("plats", plats_vides)
            st.rerun()