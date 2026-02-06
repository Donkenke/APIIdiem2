Yes, you are absolutely correct. **The date filter is the reason.**

If you have "Obras Civiles" that are **Adjudicada** (Awarded) or old, their `Fecha Cierre` (Closing Date) is likely in the **past** (e.g., 2025). Since your filter is set to **"2026/02/06 – 2026/03/31"** (Today onwards), it strictly hides all those past records.

I have updated the code below with **two fixes**:

1. **Robust Data Loading**: Fixes the `KeyError: 'Codigo'` crash if a file is empty or not found (it now creates an empty table structure instead of crashing).
2. **Date Filter Safety**: I added a check so if the date filter is empty, it shows everything. **To see your Obras Civiles, simply clear the date filter (click the 'x' in the date picker) or select a range that includes the past.**

Here is the fixed `app.py` retaining your **exact** design from version 16 (Icons, Order, Columns):

```python
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
    # DEFINE STRUCTURE to prevent KeyError if file is empty
    empty_structure = pd.DataFrame(columns=[
        "Codigo", "Nombre", "Organismo", "Estado_Lic", "Categoria", 
        "Monto_Num", "Monto", "Monto_Tipo", 
        "Fecha Pub", "FechaPubObj", "Fecha Cierre", "FechaCierreObj", "URL"
    ])

    if not os.path.exists(filepath):
        return empty_structure, {}
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception:
        return empty_structure, {} # Return empty if JSON is corrupt/empty
    
    if not data:
        return empty_structure, {}

    rows = []
    full_map = {}
    today = date.today()
    
    for item in data:
        code = item.get("CodigoExterno")
        name = str(item.get("Nombre", "")).title()
        org_name = str(item.get("Comprador", {}).get("NombreOrganismo", "")).title()
        
        # New: Capture Estado (e.g. Publicada, Adjudicada)
        estado_lic = str(item.get("Estado", "Publicada")).title()
        
        # Category
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
                     f_cierre_str = f"🔴 {f_cierre_str}" # Expired
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

# Combine maps for lookup
full_map = {**map_main, **map_obras}
hidden_ids, saved_ids, history_ids = get_db_lists()

# ==========================================
# 🔄 DATAFRAME PREP HELPER
# ==========================================
def prepare_view(df_in, sort_by="FechaPubObj"):
    if df_in.empty: return pd.DataFrame()
    
    # 1. Filter Hidden
    if "Codigo" in df_in.columns:
        df_out = df_in[~df_in["Codigo"].isin(hidden_ids)].copy()
    else:
        return pd.DataFrame()
    
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
        if row['Monto_Tipo'] == 'Estimado': return 'color: #d97706; font-weight: bold;'
        if row['Monto_Tipo'] == 'Exacto': return 'color: #16a34a; font-weight: bold;'
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

# Sidebar Filters
with st.sidebar:
    st.title("🎛️ Filtros")
    
    # Use Main Data for Filter Options (covers most cases)
    if not df_main.empty:
        valid_dates = df_main["FechaCierreObj"].dropna()
        if not valid_dates.empty:
            min_d, max_d = valid_dates.min(), valid_dates.max()
            date_range = st.date_input("📅 Fecha de Cierre", [min_d, max_d])
        else:
            date_range = []
        
        all_cats = sorted(df_main["Categoria"].astype(str).unique().tolist())
        sel_cats = st.multiselect("🏷️ Categoría", all_cats)
        
        all_orgs = sorted(df_main["Organismo"].astype(str).unique().tolist())
        sel_orgs = st.multiselect("🏢 Organismo", all_orgs)
    else:
        date_range = []
        sel_cats = []
        sel_orgs = []

    st.divider()
    if st.button("🔄 Refrescar Datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# Apply Filters Function
def filter_df(df_target):
    if df_target.empty: return df_target
    df_res = df_target.copy()
    
    # Date Filter Logic: STRICT
    if len(date_range) == 2:
        df_res = df_res[
            (df_res["FechaCierreObj"] >= date_range[0]) & 
            (df_res["FechaCierreObj"] <= date_range[1])
        ]
        
    if sel_cats: df_res = df_res[df_res["Categoria"].isin(sel_cats)]
    if sel_orgs: df_res = df_res[df_res["Organismo"].isin(sel_orgs)]
    return df_res

# Search Dropdown
all_search_codes = []
if not df_main.empty: all_search_codes.extend(df_main["Codigo"].tolist())
if not df_obras.empty: all_search_codes.extend(df_obras["Codigo"].tolist())
all_search_codes = sorted(list(set(all_search_codes)))

with st.expander("🔎 Ver Detalle (Buscar por ID)", expanded=False):
    sel_code = st.selectbox("ID:", [""] + all_search_codes, format_func=lambda x: f"{x} - {full_map.get(x, {}).get('Nombre','')[:60]}..." if x else "Seleccionar...")
    if sel_code and sel_code != st.session_state.selected_code:
        st.session_state.selected_code = sel_code

# Tabs
tab_main, tab_obras, tab_saved, tab_detail = st.tabs(["📥 Disponibles", "🚧 Obras Civiles", "⭐ Guardadas", "📄 Ficha Técnica"])

# Column Configs
base_cfg = {
    "URL": st.column_config.LinkColumn("Link", display_text="🔗", width="small"),
    "Guardar": st.column_config.CheckboxColumn("💾", width="small"),
    "Ocultar": st.column_config.CheckboxColumn("🗑️", width="small"),
    "Visto": st.column_config.CheckboxColumn("👁️", width="small", disabled=True),
    "Codigo": st.column_config.TextColumn("ID", width="small"),
    "Nombre": st.column_config.TextColumn("Nombre Licitación", width="large"),
    "Organismo": st.column_config.TextColumn("Organismo", width="medium"),
    "Monto": st.column_config.TextColumn("Monto ($)", width="medium", disabled=True), 
    "Fecha Pub": st.column_config.TextColumn("Publicado", width="small"),
    "Fecha Cierre": st.column_config.TextColumn("Cierre", width="small"),
    "Categoria": st.column_config.TextColumn("Categoría", width="medium"),
}

# Config for Obras (Includes Estado)
obras_cfg = base_cfg.copy()
obras_cfg["Estado_Lic"] = st.column_config.TextColumn("Estado", width="small")

# Column Orders
order_main = ["URL", "Guardar", "Ocultar", "Visto", "Codigo", "Nombre", "Organismo", "Monto", "Fecha Pub", "Fecha Cierre", "Categoria"]
order_obras = ["URL", "Guardar", "Ocultar", "Visto", "Codigo", "Nombre", "Organismo", "Estado_Lic", "Monto", "Fecha Pub", "Fecha Cierre"]

# --- TAB 1: MAIN ---
with tab_main:
    st.caption("Montos: **Verde** (Exacto), **Naranjo** (Estimado).")
    df_m_filtered = filter_df(df_main)
    df_m_final = prepare_view(df_m_filtered)
    
    if not df_m_final.empty:
        ed_m = st.data_editor(
            apply_text_color(df_m_final),
            column_config=base_cfg, column_order=order_main,
            hide_index=True, use_container_width=True, height=600, key="main"
        )
        if handle_grid_changes(ed_m, df_m_final): st.rerun()
    else:
        st.info("Sin registros.")

# --- TAB 2: OBRAS ---
with tab_obras:
    st.caption("Filtro: Items 'Obras Civiles'. Incluye Adjudicadas.")
    df_o_filtered = filter_df(df_obras)
    df_o_final = prepare_view(df_o_filtered)
    
    if not df_o_final.empty:
        ed_o = st.data_editor(
            apply_text_color(df_o_final),
            column_config=obras_cfg, column_order=order_obras,
            hide_index=True, use_container_width=True, height=600, key="obras"
        )
        if handle_grid_changes(ed_o, df_o_final): st.rerun()
    else:
        st.info("Sin registros. (Intente borrar el filtro de Fecha si busca licitaciones antiguas/adjudicadas)")

# --- TAB 3: SAVED ---
with tab_saved:
    st.caption("Mis licitaciones guardadas.")
    # Combine unique codes for saved view
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
                 st.markdown(f"**Monto (API):** :green[{format_clp(float(m_est))}]")
             else:
                 p_text = sec1.get('Presupuesto')
                 p_clean = clean_money_string(p_text)
                 if p_clean > 0:
                     st.markdown(f"**Presupuesto (Base):** :green[{format_clp(p_clean)}]")
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

```
