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

# Custom CSS for Alignment and Styling
st.markdown("""
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 2rem; }
        div.stButton > button:first-child { border-radius: 5px; }
        
        /* Center headers for specific columns */
        th[aria-label="Link"], th[aria-label="💾"], th[aria-label="🗑️"], th[aria-label="👁️"] {
            text-align: center !important;
        }
        
        /* Center content cells for the first few columns */
        [data-testid="stDataFrame"] table tbody td:nth-child(1),
        [data-testid="stDataFrame"] table tbody td:nth-child(2),
        [data-testid="stDataFrame"] table tbody td:nth-child(3),
        [data-testid="stDataFrame"] table tbody td:nth-child(4) {
            text-align: center !important;
        }
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
# 🧠 CATEGORIZATION LOGIC
# ==========================================
def get_category(text):
    if not text: return "General"
    text = text.upper()
    if re.search(r'\b(AIF|AIT|ATIF|ATOD|AFOS|ATO|ITO)\b', text): return "Inspección Técnica"
    if re.search(r'\b(PACC|PCC)\b', text): return "Sustentabilidad"
    if any(x in text for x in ["ASESORÍA INSPECCIÓN", "SUPERVISIÓN CONSTRUCCIÓN"]): return "Inspección Técnica"
    if any(x in text for x in ["ESTRUCTURAL", "MECÁNICA SUELOS", "GEOLÓGICO", "GEOTÉCNICO", "ENSAYOS", "LABORATORIO"]): return "Ingeniería y Lab"
    if any(x in text for x in ["TOPOGRÁFICO", "TOPOGRAFÍA", "LEVANTAMIENTO", "AEROFOTOGRAMETRÍA"]): return "Topografía"
    if any(x in text for x in ["ARQUITECTURA", "DISEÑO ARQUITECTÓNICO"]): return "Arquitectura"
    if any(x in text for x in ["EFICIENCIA ENERGÉTICA", "CERTIFICACIÓN", "SUSTENTABLE"]): return "Sustentabilidad"
    if any(x in text for x in ["MODELACIÓN", "BIM", "COORDINACIÓN DIGITAL"]): return "BIM / Modelación"
    return "Otras Civiles"

# ==========================================
# 🛠️ DATA PROCESSING
# ==========================================
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
        try:
            return float(matches[0].replace(".", "")) * UTM_VALUE
        except: pass
    return 0

def format_clp(val):
    if not val or val == 0: return "$ 0"
    return f"${val:,.0f}".replace(",", ".")

@st.cache_data
def load_data(filepath):
    # PREVENT KEY ERROR: Ensure basic structure exists even if file is missing/empty
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
    except Exception:
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
            if monto > 0:
                monto_tipo = "Exacto"
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
                delta = (f_cierre_obj - today).days
                if delta < 0:
                     f_cierre_str = f" {f_cierre_str}" # Expired
                elif 0 <= delta <= 7:
                    f_cierre_str = f"⚠️ {f_cierre_str}" # Warning
            except: pass

        rows.append({
            "Codigo": code,
            "Nombre": name,
            "Organismo": org_name,
            "Estado_Lic": estado_lic, # New Column
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

# Load Data Frames (Main + Obras)
df_main, map_main = load_data(JSON_FILE_MAIN)
df_obras, map_obras = load_data(JSON_FILE_OBRAS)

full_map = {**map_main, **map_obras}
hidden_ids, saved_ids, history_ids = get_db_lists()

# ==========================================
# 🔄 DATAFRAME PREP HELPER
# ==========================================
def prepare_view(df_in, sort_by="FechaPubObj"):
    if df_in.empty: return pd.DataFrame()
    
    # Check if 'Codigo' exists to prevent crash on empty/malformed inputs
    if "Codigo" not in df_in.columns:
        return pd.DataFrame()

    # 1. Filter Hidden
    df_out = df_in[~df_in["Codigo"].isin(hidden_ids)].copy()
    
    # 2. Logic for Visto/Nuevo
    new_mask = ~df_out["Codigo"].isin(history_ids)
    df_out["Visto"] = True 
    df_out.loc[new_mask, "Visto"] = False 
    
    # 3. Logic for Saved/Hidden Columns
    df_out["Guardar"] = df_out["Codigo"].isin(saved_ids)
    df_out["Ocultar"] = False
    
    # 4. Mark New as Seen (Side Effect)
    if any(new_mask):
        db_mark_seen(df_out.loc[new_mask, "Codigo"].tolist())
    
    return df_out.sort_values(by=[sort_by], ascending=False)

def apply_text_color(df):
    def color_monto(row):
        # Uses Gray for Exact and Orange for Estimated
        if row['Monto_Tipo'] == 'Estimado': return 'color: #d97706; font-weight: bold;'
        if row['Monto_Tipo'] == 'Exacto': return 'color: #808080; font-weight: bold;'
        return ''
    return df.style.apply(lambda row: [color_monto(row) if col == 'Monto' else '' for col in row.index], axis=1)

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

# GLOBAL CONTROLS (Sidebar)
with st.sidebar:
    st.title("🎛️ Control")
    if st.button("🔄 Recargar Datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# SEARCH (Global)
all_search_codes = []
if not df_main.empty: all_search_codes.extend(df_main["Codigo"].tolist())
if not df_obras.empty: all_search_codes.extend(df_obras["Codigo"].tolist())
all_search_codes = sorted(list(set(all_search_codes)))

with st.expander("🔎 Buscar Detalle Global (Todos los Registros)", expanded=False):
    sel_code = st.selectbox("Escriba ID o Nombre:", [""] + all_search_codes, format_func=lambda x: f"{x} - {full_map.get(x, {}).get('Nombre','')[:60]}..." if x else "Seleccionar...")
    if sel_code and sel_code != st.session_state.selected_code:
        st.session_state.selected_code = sel_code

# TABS
tab_main, tab_obras, tab_saved, tab_detail, tab_audit = st.tabs(["📥 Disponibles", "🚧 Obras Civiles", "⭐ Guardadas", "📄 Ficha Técnica", "🛠️ Auditoría Data"])

# COL CONFIGS
base_cfg = {
    "URL": st.column_config.LinkColumn("Link", display_text="🌐", width="small"),
    "Guardar": st.column_config.CheckboxColumn("💾", width="small"),
    "Ocultar": st.column_config.CheckboxColumn("🗑️", width="small"),
    "Visto": st.column_config.CheckboxColumn("Visto", width="small", disabled=True),
    "Codigo": st.column_config.TextColumn("ID", width="small"),
    "Nombre": st.column_config.TextColumn("Nombre Licitación", width="large"),
    "Organismo": st.column_config.TextColumn("Organismo", width="medium"),
    "Monto": st.column_config.TextColumn("Monto ($)", width="medium", disabled=True), 
    "Fecha Pub": st.column_config.TextColumn("Publicado", width="small"),
    "Fecha Cierre": st.column_config.TextColumn("Cierre", width="small"),
    "Categoria": st.column_config.TextColumn("Categoría", width="medium"),
}
obras_cfg = base_cfg.copy()
obras_cfg["Estado_Lic"] = st.column_config.TextColumn("Estado", width="small")

order_main = ["URL", "Guardar", "Ocultar",  "Codigo", "Nombre", "Organismo", "Monto", "Fecha Pub", "Fecha Cierre", "Categoria", "Visto"]
order_obras = ["URL", "Guardar", "Ocultar", "Codigo", "Nombre", "Organismo", "Estado_Lic", "Monto", "Fecha Cierre", "Visto"]

# --- TAB 1: DISPONIBLES (Has Date Filter) ---
with tab_main:
    # 1. LOCAL FILTERS FOR MAIN
    c1, c2, c3 = st.columns(3)
    
    # Calculate Defaults based on MAIN data
    if not df_main.empty:
        valid_dates = df_main["FechaCierreObj"].dropna()
        min_d = valid_dates.min() if not valid_dates.empty else date.today()
        max_d = valid_dates.max() if not valid_dates.empty else date.today()
        all_cats = sorted(df_main["Categoria"].astype(str).unique().tolist())
        all_orgs = sorted(df_main["Organismo"].astype(str).unique().tolist())
    else:
        min_d, max_d = date.today(), date.today()
        all_cats, all_orgs = [], []

    with c1:
        date_range_m = st.date_input("📅 Fecha Cierre", [min_d, max_d], key="date_main")
    with c2:
        sel_cats_m = st.multiselect("🏷️ Categoría", all_cats, key="cat_main")
    with c3:
        sel_orgs_m = st.multiselect("🏢 Organismo", all_orgs, key="org_main")

    st.caption("Montos: **Gris** (Exacto), **Naranjo** (Estimado).")

    # 2. APPLY MAIN FILTERS
    df_m_view = df_main.copy()
    if not df_m_view.empty:
        # Strict Date Filter only here
        if len(date_range_m) == 2:
            df_m_view = df_m_view[
                (df_m_view["FechaCierreObj"] >= date_range_m[0]) & 
                (df_m_view["FechaCierreObj"] <= date_range_m[1])
            ]
        if sel_cats_m: df_m_view = df_m_view[df_m_view["Categoria"].isin(sel_cats_m)]
        if sel_orgs_m: df_m_view = df_m_view[df_m_view["Organismo"].isin(sel_orgs_m)]
    
    # 3. RENDER
    df_m_final = prepare_view(df_m_view)
    if not df_m_final.empty:
        ed_m = st.data_editor(
            apply_text_color(df_m_final),
            column_config=base_cfg, column_order=order_main,
            hide_index=True, use_container_width=True, height=600, key="main"
        )
        if handle_grid_changes(ed_m, df_m_final): st.rerun()
    else:
        st.info("Sin registros con los filtros actuales.")

# --- TAB 2: OBRAS CIVILES (NO Date Filter) ---
with tab_obras:
    # 1. LOCAL FILTER (Organismo Only)
    c_o1, c_o2 = st.columns([1, 2])
    
    if not df_obras.empty:
        all_orgs_o = sorted(df_obras["Organismo"].astype(str).unique().tolist())
    else:
        all_orgs_o = []

    with c_o1:
        # NO Date Input here -> "Immune to Date Filter"
        sel_orgs_o = st.multiselect("🏢 Filtrar por Organismo", all_orgs_o, key="org_obras")
        
    st.caption("Filtro: Items 'Obras Civiles'. Muestra todo el historial (incluyendo Adjudicadas y Vencidas).")
    
    # 2. APPLY FILTER
    df_o_view = df_obras.copy()
    if not df_o_view.empty:
        if sel_orgs_o: 
            df_o_view = df_o_view[df_o_view["Organismo"].isin(sel_orgs_o)]
    
    # 3. RENDER
    df_o_final = prepare_view(df_o_view)
    if not df_o_final.empty:
        ed_o = st.data_editor(
            apply_text_color(df_o_final),
            column_config=obras_cfg, column_order=order_obras,
            hide_index=True, use_container_width=True, height=600, key="obras"
        )
        if handle_grid_changes(ed_o, df_o_final): st.rerun()
    else:
        st.info("No se encontraron registros de Obras Civiles.")

# --- TAB 3: SAVED ---
with tab_saved:
    st.caption("Mis licitaciones guardadas.")
    # Combine unique codes
    df_combined = pd.concat([df_main, df_obras]).drop_duplicates(subset=["Codigo"]) if not df_main.empty or not df_obras.empty else pd.DataFrame()
    
    if not df_combined.empty:
        df_s_filtered = df_combined[df_combined["Codigo"].isin(saved_ids)].copy()
        df_s_final = prepare_view(df_s_filtered)
        
        if not df_s_final.empty:
            ed_s = st.data_editor(
                apply_text_color(df_s_final),
                column_config=base_cfg, column_order=order_main,
                hide_index=True, use_container_width=True, key="saved"
            )
            if handle_grid_changes(ed_s, df_s_final): st.rerun()
        else:
            st.info("No hay licitaciones guardadas.")
    else:
         st.info("No hay datos disponibles.")

# --- TAB 4: DETAIL ---
with tab_detail:
    if st.session_state.selected_code and st.session_state.selected_code in full_map:
        code = st.session_state.selected_code
        data = full_map[code]
        
        status = "Guardado" if code in saved_ids else ("Nuevo" if code not in history_ids else "Visto")
        st.subheader(data.get("Nombre"))
        st.caption(f"ID: {code} | Estado UI: {status} | Estado Lic: {data.get('Estado')}")
        
        c_btn, _ = st.columns([1, 4])
        with c_btn:
            is_s = code in saved_ids
            if st.button("❌ Quitar" if is_s else "⭐ Guardar", key="d_btn"):
                db_toggle_save(code, not is_s)
                st.rerun()

        st.divider()
        c1, c2 = st.columns(2)
        sec1 = data.get("ExtendedMetadata", {}).get("Section_1_Características", {})
        fechas = data.get("Fechas") or {}

        with c1:
             st.markdown(f"**Organismo:** {str(data.get('Comprador', {}).get('NombreOrganismo', '-')).title()}")
             st.markdown(f"**Tipo:** {sec1.get('Tipo de Licitación', '-')}")
             st.markdown(f"**Cierre:** :red[{fechas.get('FechaCierre', 'No informado')}]")
        with c2:
             st.markdown(f"[🔗 Link MercadoPúblico]({data.get('URL_Publica')})")
             m_est = data.get("MontoEstimado")
             if m_est and float(m_est) > 0:
                 st.markdown(f"**Monto (API):** :gray[{format_clp(float(m_est))}]")
             else:
                 p_text = sec1.get('Presupuesto')
                 p_clean = clean_money_string(p_text)
                 if p_clean > 0:
                     st.markdown(f"**Presupuesto (Base):** :gray[{format_clp(p_clean)}]")
                 else:
                     est_val = estimate_monto(sec1.get('Tipo de Licitación', ''))
                     if est_val > 0:
                         st.markdown(f"**Monto (Estimado):** :orange[{format_clp(est_val)}]")
                     else:
                         st.markdown("**Monto:** No informado")

        st.info(data.get("Descripcion", "Sin descripción"))
        items = data.get('Items', {}).get('Listado', []) or data.get('DetalleArticulos', [])
        if items:
            st.markdown("###### Items")
            st.dataframe(pd.json_normalize(items), use_container_width=True)
    else:
        st.markdown("<br><h3 style='text-align:center; color:#ccc'>👈 Selecciona un ID arriba</h3>", unsafe_allow_html=True)

# --- TAB 5: AUDIT (FIXED) ---
with tab_audit:
    st.subheader("🕵️ Auditoría de Carga de Datos (Obras Civiles)")
    st.markdown("Tabla de diagnóstico para ver por qué se filtran las filas.")
    
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
            
            # Step 1: Loaded in DF?
            is_loaded = code in df_ids
            
            # Step 2: Hidden in DB?
            is_hidden = code in hidden_ids
            
            # Step 3: Filtered by UI?
            # Manually check if the logic in Tab 2 filters it out
            is_filtered_ui = False
            if is_loaded:
                # Get the row from the DF
                row = df_obras[df_obras["Codigo"] == code].iloc[0]
                
                # Check Org Filter - FIXED VARIABLE NAME (sel_orgs_o)
                if sel_orgs_o and row["Organismo"] not in sel_orgs_o:
                    is_filtered_ui = True
                
                # We specifically removed date filter for Obras, so checking date is not needed here

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
