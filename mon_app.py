import streamlit as st
import json
import pandas as pd
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import time

# ==============================================================================
# 1. CONNEXION CLOUD (CACHE)
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

# ==============================================================================
# 2. GESTION INTELLIGENTE DES DONNÉES (CACHE LOCAL)
# ==============================================================================

def fetch_from_cloud(key):
    """Lit les données depuis Google Sheets (Attention aux quotas !)"""
    try:
        client = get_gspread_client()
        sh = client.open("ProjetPoids_DB")
        worksheet = sh.worksheet(TABS_MAPPING[key])
        raw_data = worksheet.acell('A1').value
        if not raw_data: return {}
        return json.loads(raw_data)
    except Exception as e:
        return {}

def push_to_cloud(key, data):
    """Envoie les données vers Google Sheets"""
    try:
        client = get_gspread_client()
        sh = client.open("ProjetPoids_DB")
        worksheet = sh.worksheet(TABS_MAPPING[key])
        json_str = json.dumps(data, ensure_ascii=False)
        worksheet.update_acell('A1', json_str)
    except Exception as e:
        st.error(f"Erreur sauvegarde cloud: {e}")

def init_state():
    """Charge tout au démarrage UNE SEULE FOIS"""
    keys = ["recettes", "plats", "planning", "journal", "poids", "garde_manger"]
    
    # On vérifie si c'est déjà chargé pour ne pas rappeler Google
    if "data_loaded" not in st.session_state:
        with st.spinner('Chargement des données...'):
            for k in keys:
                st.session_state[k] = fetch_from_cloud(k)
                time.sleep(0.2) # Petite pause pour être gentil avec Google
            
            # Init Garde Manger par défaut si vide
            if not st.session_state["garde_manger"]:
                st.session_state["garde_manger"] = DEFAULTS_PANTRY.copy()
                push_to_cloud("garde_manger", st.session_state["garde_manger"])
                
            st.session_state["data_loaded"] = True

def save_data(key, new_data):
    """Met à jour le cache local ET le cloud"""
    st.session_state[key] = new_data # Mise à jour immédiate (rapide)
    push_to_cloud(key, new_data) # Mise à jour cloud (lente)

# Données par défaut
DEFAULTS_PANTRY = {
    "Pâtes / Riz (Cru)": 360, "Pâtes / Riz (Cuit)": 130, "Pomme de terre": 80, 
    "Légumes": 40, "Pain": 260, "Boeuf 5%": 125, "Poulet": 110, "Oeuf": 70,
    "Crème 15%": 160, "Huile": 900, "Fromage râpé": 380, "Yaourt": 50, "Banane": 89
}

def get_today_str(): return datetime.now().strftime("%Y-%m-%d")

# ==============================================================================
# 3. INTERFACE
# ==============================================================================
st.set_page_config(page_title="Le Portionneur V12", page_icon="⚡", layout="wide")
st.title("⚡ Le Portionneur : Rapide")

# Initialisation unique
init_state()

# Raccourcis vers le state (pour faciliter le code)
recettes = st.session_state["recettes"]
plats_vides = st.session_state["plats"]
planning = st.session_state["planning"]
journal = st.session_state["journal"]
poids_data = st.session_state["poids"]
pantry = st.session_state["garde_manger"]

MOMENTS = ["Matin", "Midi", "Collation", "Soir"]
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]

# --- BOUTON DE RECHARGEMENT ---
# Utile si tu modifies les données sur un autre appareil et que tu veux forcer la mise à jour
if st.sidebar.button("🔄 Forcer la synchro Cloud"):
    for k in ["data_loaded", "recettes", "plats", "planning", "journal", "poids", "garde_manger"]:
        if k in st.session_state: del st.session_state[k]
    st.rerun()

tabs = st.tabs(["🏠 Cockpit", "⚖️ Poids", "📊 Histo", "🛒 Courses", "📅 Planning", "👨‍🍳 Recettes", "🥫 Garde-Manger", "🧽 Plats"])

# --- 1. COCKPIT ---
with tabs[0]:
    col_main, col_trk = st.columns([2, 1])
    today = get_today_str()
    if today not in journal: journal[today] = []
    tot = sum([e['kcal'] for e in journal[today]])
    
    with col_trk:
        st.metric("Aujourd'hui", f"{int(tot)} kcal")
        st.progress(min(tot/2200, 1.0))
        with st.expander("⚡ Express"):
            xn = st.text_input("Quoi", key="xn")
            xk = st.number_input("Kcal", 0, step=50, key="xk")
            if st.button("Ajouter", key="xb") and xn:
                journal[today].append({"heure": datetime.now().strftime("%H:%M"), "recette": f"⚡ {xn}", "poids": 0, "kcal": xk})
                save_data("journal", journal)
                st.success("Ok")
                st.rerun()

    with col_main:
        if not recettes: st.warning("Crée des recettes !")
        else:
            # Auto-detect (UTC+1 rough fix)
            now = datetime.now() + timedelta(hours=1)
            jour = JOURS[now.weekday()]
            h = now.hour
            mom = "Matin" if h<11 else "Midi" if h<15 else "Collation" if h<18 else "Soir"
            
            idx, obj, msg = 0, 600, None
            if jour in planning and mom in planning[jour]:
                p = planning[jour][mom]
                if p["recette"] in recettes:
                    idx = sorted(list(recettes.keys())).index(p["recette"])
                    obj = p["cible"]
                    msg = f"📅 {jour} {mom} : {p['recette']}"
            if msg: st.info(msg)

            ch = st.selectbox("Plat", sorted(list(recettes.keys())), index=idx, key="m_s")
            ct = recettes[ch]['total_cal']
            st.write("---")
            c1, c2 = st.columns(2)
            with c1:
                tr = st.checkbox("Tare", True, key="m_t")
                pa = st.number_input("Poids Balance", 1000, step=10, key="m_p")
                pn = pa
                if tr and plats_vides:
                    pl = st.selectbox("Contenant", list(plats_vides.keys()), key="m_pl")
                    pn = max(0, pa - plats_vides[pl])
                    st.caption(f"Net: **{pn}g**")
            with c2: ob = st.number_input("Objectif", value=obj, step=50, key="m_o")
            
            if pn > 0:
                por = (ob / ct) * pn
                st.success(f"👉 Portion : **{int(por)} g**")
                if st.button("✅ Valider", type="primary", key="m_v"):
                    journal[today].append({"heure": now.strftime("%H:%M"), "recette": ch, "poids": int(por), "kcal": ob})
                    save_data("journal", journal)
                    st.rerun()

# --- 2. POIDS ---
with tabs[1]:
    w = st.number_input("Kg", 0.0, 200.0, step=0.1, format="%.1f", key="wp")
    if st.button("Sauver", key="ws") and w>0:
        poids_data[today] = w
        save_data("poids", poids_data)
        st.success("Sauvé")
        st.rerun()
    if poids_data:
        dts = sorted(poids_data.keys())
        st.line_chart(pd.DataFrame({"Date": dts, "Poids": [poids_data[d] for d in dts]}).set_index("Date"))

# --- 3. HISTO ---
with tabs[2]:
    if journal[today]:
        for i in journal[today]: st.write(f"{i['heure']} | {i['recette']} | {i['kcal']}")
        if st.button("Clear Today", key="hc"):
            journal[today] = []
            save_data("journal", journal)
            st.rerun()
    st.write("---")
    dg = {}
    for i in range(6, -1, -1):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        dg[d] = sum([x['kcal'] for x in journal.get(d, [])])
    st.bar_chart(dg)

# --- 4. COURSES ---
with tabs[3]:
    if st.button("Générer", key="cg"):
        sh = {}
        for j in planning:
            for m in planning[j]:
                r = planning[j][m]["recette"]
                if r in recettes:
                    for i in recettes[r]["ingredients"]: sh[i["nom"]] = sh.get(i["nom"], 0) + i["poids"]
        for k,v in sh.items(): st.write(f"- {k}: {v}g")

# --- 5. PLANNING ---
with tabs[4]:
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
            save_data("planning", np)
            st.rerun()

# --- 6. RECETTES ---
with tabs[5]:
    sr = st.text_input("🔍", key="rs")
    ls = sorted(list(recettes.keys()))
    if sr: ls = [x for x in ls if sr.lower() in x.lower()]
    with st.expander("Liste"): st.table([{"Nom": x, "Kcal": recettes[x]['total_cal']} for x in ls])
    
    cg, cd = st.columns([1, 2])
    with cg:
        md = st.radio("Action", ["Nouvelle", "Modifier", "Dupliquer", "Supprimer"], key="rm")
        tg = None
        if md != "Nouvelle":
            tg = st.selectbox("Cible", ls, key="rt")
            if md == "Supprimer" and st.button("Suppr", key="rd"):
                del recettes[tg]
                save_data("recettes", recettes)
                st.rerun()
    with cd:
        if md != "Supprimer":
            if 'ti' not in st.session_state or st.session_state.get('lm') != md or st.session_state.get('lt') != tg:
                st.session_state.ti = []
                if (md == "Modifier" or md == "Dupliquer") and tg: st.session_state.ti = recettes[tg]["ingredients"].copy()
                st.session_state.lm = md
                st.session_state.lt = tg
            
            dn = tg if md == "Modifier" and tg else f"{tg} (Copie)" if md=="Dupliquer" and tg else ""
            rn = st.text_input("Nom", value=dn, disabled=(md=="Modifier"), key="rn")
            
            st.markdown("##### Ingrédients")
            ca, cb, cc, cd = st.columns([3, 2, 2, 1])
            ni = ca.selectbox("Ajout", [""] + sorted(list(pantry.keys())), key="ra_n")
            np = cb.number_input("g", 0, step=10, key="ra_p")
            nc = cc.number_input("kcal", value=int((np*pantry[ni])/100) if ni and ni in pantry else 0, key="ra_k")
            if cd.button("➕", key="ra_b") and ni: st.session_state.ti.append({"nom": ni, "poids": np, "cal": nc})
            
            st.write("---")
            rdl = -1
            tot = 0
            for i, x in enumerate(st.session_state.ti):
                c1, c2, c3, c4 = st.columns([4, 2, 2, 1])
                c1.text(x['nom'])
                c2.text(f"{x['poids']}g")
                c3.text(f"{x['cal']}kcal")
                tot += x['cal']
                if c4.button("🗑️", key=f"rrm_{i}"): rdl = i
            if rdl >= 0:
                st.session_state.ti.pop(rdl)
                st.rerun()
            st.info(f"Total: {tot}")
            if st.button("💾 Sauver", type="primary", key="rsav") and rn:
                recettes[rn] = {"total_cal": tot, "ingredients": st.session_state.ti}
                save_data("recettes", recettes)
                st.success("Sauvé")
                if md in ["Nouvelle", "Dupliquer"]:
                    st.session_state.ti = []
                    st.rerun()

# --- 7. GARDE MANGER ---
with tabs[6]:
    with st.expander("Ajout"):
        cn = st.text_input("Nom", key="gmn")
        ck = st.number_input("Kcal", 0, key="gmk")
        if st.button("Ajouter", key="gma") and cn:
            pantry[cn] = ck
            save_data("garde_manger", pantry)
            st.rerun()
    sch = st.text_input("Recherche", key="gms")
    its = pantry.items()
    if sch: its = {k:v for k,v in pantry.items() if sch.lower() in k.lower()}.items()
    for k, v in sorted(its):
        c1, c2, c3, c4 = st.columns([3, 2, 1, 1])
        nn = c1.text_input(f"n", k, label_visibility="collapsed", key=f"gn_{k}")
        nk = c2.number_input(f"v", value=v, label_visibility="collapsed", key=f"gk_{k}")
        if nn != k or nk != v:
            if c3.button("💾", key=f"gs_{k}"):
                if nn != k: del pantry[k]
                pantry[nn] = nk
                save_data("garde_manger", pantry)
                st.rerun()
        if c4.button("🗑️", key=f"gd_{k}"):
            del pantry[k]
            save_data("garde_manger", pantry)
            st.rerun()

# --- 8. PLATS ---
with tabs[7]:
    pn = st.text_input("Nom", key="pn")
    pw = st.number_input("Poids", 0, key="pw")
    if st.button("Ajouter", key="pa") and pn:
        plats_vides[pn] = pw
        save_data("plats", plats_vides)
        st.rerun()
    for k,v in plats_vides.items():
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.text(k)
        c2.text(v)
        if c3.button("X", key=f"pd_{k}"):
            del plats_vides[k]
            save_data("plats", plats_vides)
            st.rerun()
