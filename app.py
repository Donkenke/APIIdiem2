import streamlit as st
import pandas as pd
import json
import os
import re
import sqlite3
from datetime import datetime, date

# --- CONFIGURATION ---
st.set_page_config(page_title="Monitor Licitaciones IDIEM", layout="wide", page_icon="🏗️")

UTM_VALUE = 69611 
JSON_FILE_MAIN = "FINAL_PRODUCTION_DATA.json"
JSON_FILE_OBRAS = "OBRAS_CIVILES_DATA.json"
DB_FILE = "licitaciones_state.db"

# Custom CSS
st.markdown("""
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 2rem; }
        div.stButton > button:first-child { border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

if 'selected_code' not in st.session_state:
    st.session_state.selected_code = None

# ==========================================
# 🗄️ SQLITE DATABASE
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS hidden (code TEXT PRIMARY KEY, timestamp DATETIME)')
    c.execute('CREATE TABLE IF NOT EXISTS saved (code TEXT PRIMARY KEY, timestamp DATETIME, note TEXT)')
    c.execute('CREATE TABLE IF NOT EXISTS history (code TEXT PRIMARY KEY, first_seen DATETIME)')
    conn.commit()
    return conn

conn = init_db()

def get_db_lists():
    c = conn.cursor()
    hidden = {row[0] for row in c.execute('SELECT code FROM hidden').fetchall()}
    saved = {row[0] for row in c.execute('SELECT code FROM saved').fetchall()}
    history = {row[0] for row in c.execute('SELECT code FROM history').fetchall()}
    return hidden, saved, history

def db_toggle_save(code, action):
    c = conn.cursor()
    if action:
        c.execute('INSERT OR REPLACE INTO saved (code, timestamp) VALUES (?, ?)', (code, datetime.now()))
        c.execute('DELETE FROM hidden WHERE code = ?', (code,))
        st.toast(f"✅ Guardado: {code}")
    else:
        c.execute('DELETE FROM saved WHERE code = ?', (code,))
        st.toast(f"❌ Removido: {code}")
    conn.commit()

def db_hide_permanent(code):
    c = conn.cursor()
    c.execute('DELETE FROM saved WHERE code = ?', (code,))
    c.execute('INSERT OR REPLACE INTO hidden (code, timestamp) VALUES (?, ?)', (code, datetime.now()))
    conn.commit()
    st.toast(f"🗑️ Ocultado: {code}")

def db_mark_seen(codes):
    if not codes: return
    c = conn.cursor()
    now = datetime.now()
    data = [(c, now) for c in codes]
    c.executemany('INSERT OR IGNORE INTO history (code, first_seen) VALUES (?, ?)', data)
    conn.commit()

# ==========================================
# 🧠 CATEGORIZATION & UTILS
# ==========================================
def get_category(text):
    if not text: return "General"
    text = text.upper()
    if re.search(r'\b(AIF|AIT|ATIF|ATOD|AFOS|ATO|ITO)\b', text): return "Inspección Técnica"
    if re.search(r'\b(PACC|PCC)\b', text): return "Sustentabilidad"
    if any(x in text for x in ["ASESORÍA INSPECCIÓN", "SUPERVISIÓN CONSTRUCCIÓN"]): return "Inspección Técnica"
    if any(x in text for x in ["ESTRUCTURAL", "MECÁNICA SUELOS", "GEOLÓGICO", "GEOTÉCNICO", "ENSAYOS", "LABORATORIO"]): return "Ingeniería y Lab"
    if any(x in text for x in ["TOPOGRÁFICO", "TOPOGRAFÍA", "LEVANTAMIENTO", "AEROFOTOGRAMETRÍA"]): return "Topografía"
    return "Otras Civiles"

def clean_money_string(text):
    if not text: return 0
    try:
        clean = re.sub(r'[^\d]', '', str(text))
        if clean: return float(clean)
    except: pass
    return 0

def estimate_monto(text):
    if not text: return 0
    matches = re.findall(r'(\d[\d\.]*)', text)
    if matches:
        try: return float(matches[0].replace(".", "")) * UTM_VALUE
        except: pass
    return 0

def format_clp(val):
    if not val or val == 0: return "$ 0"
    return f"${val:,.0f}".replace(",", ".")

# ==========================================
# 🛠️ DATA LOADING
# ==========================================
@st.cache_data
def load_data(filepath):
    # Setup columns to prevent KeyErrors if empty
    expected_cols = [
        "Codigo", "Nombre", "Organismo", "Estado_Lic", "Categoria", 
        "Monto_Num", "Monto", "Monto_Tipo", 
        "Fecha Pub", "FechaPubObj", "Fecha Cierre", "FechaCierreObj", "URL"
    ]
    
    if not os.path.exists(filepath):
        return pd.DataFrame(columns=expected_cols), {}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except:
        return pd.DataFrame(columns=expected_cols), {}
        
    if not data:
        return pd.DataFrame(columns=expected_cols), {}
    
    rows = []
    full_map = {}
    today = date.today()
    
    for item in data:
        code = item.get("CodigoExterno")
        name = str(item.get("Nombre", "")).title()
        org_name = str(item.get("Comprador", {}).get("NombreOrganismo", "")).title()
        estado_lic = str(item.get("Estado", "Publicada")).title()
        
        # Categorization
        cat = item.get("Match_Category")
        if not cat or cat == "Sin Categoría":
            cat = get_category(name)
        
        # Monto Logic
        monto = 0
        monto_tipo = "Exacto"
        if item.get("MontoEstimado") and float(item.get("MontoEstimado") or 0) > 0:
            monto = float(item.get("MontoEstimado"))
            monto_tipo = "Exacto"
        else:
            ext = item.get("ExtendedMetadata", {}).get("Section_1_Características", {})
            monto = clean_money_string(ext.get("Presupuesto"))
            if monto > 0: monto_tipo = "Exacto"
            else:
                monto = estimate_monto(ext.get("Tipo de Licitación", ""))
                if monto > 0: monto_tipo = "Estimado"

        # Dates
        fechas = item.get("Fechas") or {}
        raw_pub = fechas.get("FechaPublicacion")
        f_pub_str = str(raw_pub)[:10] if raw_pub else ""
        f_pub_obj = None
        if f_pub_str:
            try: f_pub_obj = datetime.strptime(f_pub_str, "%Y-%m-%d").date()
            except: pass
        
        raw_cierre = fechas.get("FechaCierre")
        f_cierre_str = str(raw_cierre)[:10] if raw_cierre else ""
        f_cierre_obj = None
        if f_cierre_str:
            try:
                f_cierre_obj = datetime.strptime(f_cierre_str, "%Y-%m-%d").date()
            except: pass

        rows.append({
            "Codigo": code,
            "Nombre": name,
            "Organismo": org_name,
            "Estado_Lic": estado_lic,
            "Categoria": cat,
            "Monto_Num": monto,
            "Monto": format_clp(monto),
            "Monto_Tipo": monto_tipo,
            "Fecha Pub": f_pub_str,
            "FechaPubObj": f_pub_obj,
            "Fecha Cierre": f_cierre_str,
            "FechaCierreObj": f_cierre_obj,
            "URL": item.get("URL_Publica")
        })
        full_map[code] = item
        
    return pd.DataFrame(rows), full_map

# Load Data
df_main, map_main = load_data(JSON_FILE_MAIN)
df_obras, map_obras = load_data(JSON_FILE_OBRAS)

full_map = {**map_main, **map_obras}
hidden_ids, saved_ids, history_ids = get_db_lists()

# ==========================================
# 🔄 PREPARE VIEW HELPER
# ==========================================
def prepare_view(df_in, filter_hidden=True):
    if df_in.empty: return pd.DataFrame()
    if "Codigo" not in df_in.columns: return pd.DataFrame()

    df_out = df_in.copy()
    
    # 1. Hide Hidden (only if requested)
    if filter_hidden:
        df_out = df_out[~df_out["Codigo"].isin(hidden_ids)]
    
    # 2. Visto logic
    new_mask = ~df_out["Codigo"].isin(history_ids)
    df_out["Visto"] = True 
    df_out.loc[new_mask, "Visto"] = False 
    
    # 3. Checkboxes
    df_out["Guardar"] = df_out["Codigo"].isin(saved_ids)
    df_out["Ocultar"] = False
    
    # 4. Side Effect: Mark seen
    if filter_hidden and any(new_mask):
        db_mark_seen(df_out.loc[new_mask, "Codigo"].tolist())
    
    return df_out.sort_values(by=["FechaPubObj"], ascending=False)

def handle_grid_changes(edited, original):
    if edited["Guardar"].ne(original["Guardar"]).any():
        row = edited[edited["Guardar"] != original["Guardar"]].iloc[0]
        db_toggle_save(row["Codigo"], row["Guardar"])
        return True
    if edited["Ocultar"].eq(True).any():
        row = edited[edited["Ocultar"] == True].iloc[0]
        db_hide_permanent(row["Codigo"])
        return True
    return False

# ==========================================
# 🖥️ MAIN UI
# ==========================================
st.title("Monitor Licitaciones IDIEM")

with st.sidebar:
    st.title("🎛️ Control")
    if st.button("🔄 Recargar Datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Search
all_codes = []
if not df_main.empty: all_codes.extend(df_main["Codigo"].tolist())
if not df_obras.empty: all_codes.extend(df_obras["Codigo"].tolist())
all_codes = sorted(list(set(all_codes)))

with st.expander("🔎 Buscar Detalle Global", expanded=False):
    sel_code = st.selectbox("ID o Nombre:", [""] + all_codes)
    if sel_code: st.session_state.selected_code = sel_code

# TABS
tab_main, tab_obras, tab_saved, tab_detail, tab_audit = st.tabs(["📥 Disponibles", "🚧 Obras Civiles", "⭐ Guardadas", "📄 Ficha Técnica", "🛠️ Auditoría Data"])

base_cfg = {
    "URL": st.column_config.LinkColumn("Link", display_text="🔗", width="small"),
    "Guardar": st.column_config.CheckboxColumn("💾", width="small"),
    "Ocultar": st.column_config.CheckboxColumn("🗑️", width="small"),
    "Visto": st.column_config.CheckboxColumn("👁️", width="small", disabled=True),
    "Codigo": st.column_config.TextColumn("ID", width="small"),
    "Nombre": st.column_config.TextColumn("Nombre", width="large"),
    "Organismo": st.column_config.TextColumn("Organismo", width="medium"),
    "Monto": st.column_config.TextColumn("Monto", width="medium"),
}
obras_cfg = base_cfg.copy()
obras_cfg["Estado_Lic"] = st.column_config.TextColumn("Estado", width="small")

# --- TAB 1: MAIN ---
with tab_main:
    c1, c2, c3 = st.columns(3)
    # Defaults
    d_min, d_max = date.today(), date.today()
    cats, orgs = [], []
    if not df_main.empty:
        valid_d = df_main["FechaCierreObj"].dropna()
        if not valid_d.empty:
            d_min, d_max = valid_d.min(), valid_d.max()
        cats = sorted(df_main["Categoria"].astype(str).unique().tolist())
        orgs = sorted(df_main["Organismo"].astype(str).unique().tolist())

    with c1: d_range = st.date_input("Fecha Cierre", [d_min, d_max])
    with c2: s_cats = st.multiselect("Categoría", cats)
    with c3: s_orgs = st.multiselect("Organismo", orgs)

    df_v = df_main.copy()
    if not df_v.empty:
        if len(d_range) == 2:
            df_v = df_v[(df_v["FechaCierreObj"] >= d_range[0]) & (df_v["FechaCierreObj"] <= d_range[1])]
        if s_cats: df_v = df_v[df_v["Categoria"].isin(s_cats)]
        if s_orgs: df_v = df_v[df_v["Organismo"].isin(s_orgs)]

    df_final = prepare_view(df_v)
    if not df_final.empty:
        e = st.data_editor(df_final, column_config=base_cfg, column_order=["URL","Guardar","Ocultar","Visto","Codigo","Nombre","Organismo","Monto","Fecha Cierre","Categoria"], hide_index=True, key="main")
        if handle_grid_changes(e, df_final): st.rerun()
    else:
        st.info("Sin registros.")

# --- TAB 2: OBRAS CIVILES (NO DATE FILTER) ---
with tab_obras:
    c_o1, c_o2 = st.columns([1, 2])
    orgs_o = []
    if not df_obras.empty:
        orgs_o = sorted(df_obras["Organismo"].astype(str).unique().tolist())

    with c_o1:
        s_orgs_o = st.multiselect("Filtrar Organismo", orgs_o, key="o_org")
    
    st.caption("ℹ️ Muestra todo el historial (sin filtro de fecha).")

    df_ov = df_obras.copy()
    
    # FILTER 1: Organization
    if not df_ov.empty and s_orgs_o:
        df_ov = df_ov[df_ov["Organismo"].isin(s_orgs_o)]

    # RENDER
    # Pass this DF to prepare_view (which handles Hidden logic)
    df_of = prepare_view(df_ov, filter_hidden=True)
    
    if not df_of.empty:
        eo = st.data_editor(
            df_of, 
            column_config=obras_cfg, 
            column_order=["URL","Guardar","Ocultar","Visto","Codigo","Nombre","Organismo","Estado_Lic","Monto","Fecha Pub","Fecha Cierre"], 
            hide_index=True, 
            key="obras"
        )
        if handle_grid_changes(eo, df_of): st.rerun()
    else:
        st.warning("No se encontraron registros de Obras Civiles visibles.")

# --- TAB 3: SAVED ---
with tab_saved:
    combo = pd.concat([df_main, df_obras]).drop_duplicates("Codigo") if (not df_main.empty or not df_obras.empty) else pd.DataFrame()
    if not combo.empty:
        saved = combo[combo["Codigo"].isin(saved_ids)]
        saved_f = prepare_view(saved, filter_hidden=False) # Show even if hidden
        if not saved_f.empty:
            es = st.data_editor(saved_f, column_config=base_cfg, column_order=["URL","Guardar","Codigo","Nombre","Monto"], hide_index=True, key="saved")
            if handle_grid_changes(es, saved_f): st.rerun()
        else: st.info("Nada guardado.")

# --- TAB 4: DETAIL ---
with tab_detail:
    if st.session_state.selected_code and st.session_state.selected_code in full_map:
        code = st.session_state.selected_code
        d = full_map[code]
        st.header(d.get("Nombre"))
        st.json(d, expanded=False)
    else:
        st.write("Seleccione un código.")

# --- TAB 5: AUDIT (NEW) ---
with tab_audit:
    st.subheader("🕵️ Auditoría de Carga de Datos (Obras Civiles)")
    st.markdown("Esta tabla muestra cada registro encontrado en el JSON y por qué paso (o no pasó) al Dataframe final.")
    
    # 1. READ RAW FILE MANUALLY
    raw_list = []
    if os.path.exists(JSON_FILE_OBRAS):
        try:
            with open(JSON_FILE_OBRAS, 'r', encoding='utf-8') as f:
                raw_list = json.load(f)
        except Exception as e:
            st.error(f"Error leyendo JSON raw: {e}")
    else:
        st.error(f"Archivo {JSON_FILE_OBRAS} no encontrado.")

    st.metric("Total Registros en JSON Raw", len(raw_list))

    if raw_list:
        audit_data = []
        
        # Get set of IDs currently in the DataFrame (Successful loads)
        df_ids = set(df_obras["Codigo"].tolist()) if not df_obras.empty else set()
        
        for item in raw_list:
            code = item.get("CodigoExterno", "SIN_CODIGO")
            name = item.get("Nombre", "")
            org = item.get("Comprador", {}).get("NombreOrganismo", "")
            
            # Step 1: Loaded in DF?
            is_loaded = code in df_ids
            
            # Step 2: Hidden in DB?
            is_hidden = code in hidden_ids
            
            # Step 3: Filtered by UI? (Check against current Obras Filters)
            # Replicate the filter logic from Tab 2 manually here to check
            is_filtered_ui = False
            if is_loaded and s_orgs_o:
                # If loaded, we check the DF value. If not loaded, we can't check easily (assume False)
                row_val = df_obras[df_obras["Codigo"] == code]["Organismo"].iloc[0] if is_loaded else ""
                if row_val not in s_orgs_o:
                    is_filtered_ui = True

            # FINAL STATUS
            if not is_loaded: status = "❌ Error Parseo"
            elif is_hidden: status = "👻 Oculto en DB"
            elif is_filtered_ui: status = "🔍 Filtrado UI"
            else: status = "✅ VISIBLE"

            audit_data.append({
                "Codigo": code,
                "Nombre": name,
                "1. En JSON": True,
                "2. Parseado (DF)": is_loaded,
                "3. DB Oculto": is_hidden,
                "4. Filtro Org": is_filtered_ui,
                "RESULTADO": status
            })
        
        df_audit = pd.DataFrame(audit_data)
        
        # Display Audit Table
        st.dataframe(
            df_audit,
            column_config={
                "1. En JSON": st.column_config.CheckboxColumn(width="small"),
                "2. Parseado (DF)": st.column_config.CheckboxColumn(width="small"),
                "3. DB Oculto": st.column_config.CheckboxColumn(width="small"),
                "4. Filtro Org": st.column_config.CheckboxColumn(width="small"),
                "RESULTADO": st.column_config.TextColumn(width="medium"),
            },
            hide_index=True,
            use_container_width=True,
            height=600
        )
