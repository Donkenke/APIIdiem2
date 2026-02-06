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
JSON_FILE = "FINAL_PRODUCTION_DATA.json"
DB_FILE = "licitaciones_state.db"

# Custom CSS
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
def load_data():
    if not os.path.exists(JSON_FILE):
        return pd.DataFrame(), {}
    
    with open(JSON_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    rows = []
    full_map = {}
    today = date.today()
    
    for item in data:
        code = item.get("CodigoExterno")
        name = str(item.get("Nombre", "")).title()
        org_name = str(item.get("Comprador", {}).get("NombreOrganismo", "")).title()
        
        cat = item.get("Match_Category")
        if not cat or cat == "Sin Categoría":
            cat = get_category(name)
        
        # --- MONTO LOGIC ---
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
                if monto > 0:
                    monto_tipo = "Estimado"

        # --- DATES ---
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
                if 0 <= delta <= 7:
                    f_cierre_str = f"{f_cierre_str} ⚠️"
                elif delta < 0:
                     f_cierre_str = f"{f_cierre_str} (Cerrada)"
            except: pass

        rows.append({
            "Codigo": code,
            "Nombre": name,
            "Organismo": org_name,
            "Categoria": cat,
            "Monto_Num": monto,
            "Monto": format_clp(monto),
            "Monto_Tipo": monto_tipo, # Used for styling
            "Fecha Pub": f_pub_str,
            "FechaPubObj": f_pub_obj,
            "Fecha Cierre": f_cierre_str,
            "FechaCierreObj": f_cierre_obj,
            "URL": item.get("URL_Publica")
        })
        full_map[code] = item
        
    return pd.DataFrame(rows), full_map

# Load Data
df_raw, full_map = load_data()
hidden_ids, saved_ids, history_ids = get_db_lists()

# ==========================================
# 🔍 SIDEBAR FILTERS
# ==========================================
with st.sidebar:
    st.title("🎛️ Filtros")
    if not df_raw.empty:
        valid_dates = df_raw["FechaCierreObj"].dropna()
        if not valid_dates.empty:
            min_d, max_d = valid_dates.min(), valid_dates.max()
            date_range = st.date_input("📅 Fecha de Cierre", [min_d, max_d])
        else:
            date_range = []
            st.warning("Sin fechas válidas.")
        
        all_cats = sorted(df_raw["Categoria"].astype(str).unique().tolist())
        sel_cats = st.multiselect("🏷️ Categoría", all_cats)
        
        all_orgs = sorted(df_raw["Organismo"].astype(str).unique().tolist())
        sel_orgs = st.multiselect("🏢 Organismo", all_orgs)
    else:
        date_range = []
        sel_cats = []
        sel_orgs = []

    st.divider()
    if st.button("🔄 Refrescar Datos", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

# ==========================================
# 🖥️ MAIN UI
# ==========================================
st.title("Monitor Licitaciones IDIEM")

# Filter Logic
if not df_raw.empty:
    df_visible = df_raw[~df_raw["Codigo"].isin(hidden_ids)].copy()

    if len(date_range) == 2:
        df_visible = df_visible[
            (df_visible["FechaCierreObj"] >= date_range[0]) & 
            (df_visible["FechaCierreObj"] <= date_range[1])
        ]
    if sel_cats: df_visible = df_visible[df_visible["Categoria"].isin(sel_cats)]
    if sel_orgs: df_visible = df_visible[df_visible["Organismo"].isin(sel_orgs)]

    new_mask = ~df_visible["Codigo"].isin(history_ids)
    df_visible["Visto"] = True 
    df_visible.loc[new_mask, "Visto"] = False 
    
    df_visible["Guardar"] = df_visible["Codigo"].isin(saved_ids)
    df_visible["Ocultar"] = False
    
    new_codes = df_visible.loc[new_mask, "Codigo"].tolist()
    if new_codes: db_mark_seen(new_codes)

    df_saved_view = df_raw[df_raw["Codigo"].isin(saved_ids)].copy()
    df_saved_view["Guardar"] = True
    df_saved_view["Ocultar"] = False
    df_saved_view["Visto"] = True
else:
    df_visible = pd.DataFrame()
    df_saved_view = pd.DataFrame()

# Detail Selector
all_options = sorted(list(set(df_visible["Codigo"].tolist() + df_saved_view["Codigo"].tolist())))
with st.expander("🔎 Ver Detalle (Buscar por ID)", expanded=False):
    sel_code = st.selectbox("ID Licitación:", [""] + all_options, format_func=lambda x: f"{x} - {full_map.get(x, {}).get('Nombre','')[:60]}..." if x else "Seleccionar...")
    if sel_code and sel_code != st.session_state.selected_code:
        st.session_state.selected_code = sel_code

tab_main, tab_saved, tab_detail = st.tabs(["📥 Disponibles", "⭐ Guardadas", "📄 Ficha Técnica"])

# --- STYLING LOGIC (Text Color Only) ---
def apply_text_color(df):
    """Applies text color to Monto based on Monto_Tipo"""
    def color_monto(row):
        color = '' 
        if row['Monto_Tipo'] == 'Estimado':
            color = 'color: #16a34a; font-weight: 500;' # Orange
        elif row['Monto_Tipo'] == 'Exacto':
            color = 'color: #26221d; font-weight: 500;' # Green
        return [color if col == 'Monto' else '' for col in row.index]
    return df.style.apply(color_monto, axis=1)

def handle_changes(edited, original):
    if edited["Guardar"].ne(original["Guardar"]).any():
        row = edited[edited["Guardar"] != original["Guardar"]].iloc[0]
        db_toggle_save(row["Codigo"], row["Guardar"])
        return True
    if edited["Ocultar"].eq(True).any():
        row = edited[edited["Ocultar"] == True].iloc[0]
        db_hide_permanent(row["Codigo"])
        return True
    return False

col_config = {
    "URL": st.column_config.LinkColumn("Url", display_text="🌐", width="small"),
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
ordered = ["URL", "Guardar", "Ocultar", "Codigo", "Nombre", "Organismo", "Monto", "Fecha Pub", "Fecha Cierre", "Categoria", "Visto"]

# --- TAB 1 ---
with tab_main:
    # Simple Note (Not Labels)
    st.markdown(
    """
    <p style='text-align: right; color: grey; font-size: 12px;'>
        Nota: Los montos en <span style='color: gray;'><strong>gris</strong></span> son exactos (API), los en <span style='color: green;'><strong>verde</strong></span> son estimados.
    </p>
    """,
    unsafe_allow_html=True
    )
    
    if not df_visible.empty:
        df_disp = df_visible.sort_values(by=["FechaPubObj"], ascending=False)
        styled_df = apply_text_color(df_disp)
        
        ukey = f"main_{len(df_disp)}_{st.session_state.get('last_update',0)}"
        edited = st.data_editor(
            styled_df, 
            column_config=col_config,
            column_order=ordered,
            hide_index=True,
            use_container_width=True,
            height=600,
            key=ukey
        )
        if handle_changes(edited, df_disp):
            st.session_state.last_update = datetime.now().timestamp()
            st.rerun()

# --- TAB 2 ---
with tab_saved:
    st.markdown(
    """
    <p style='text-align: right; color: grey; font-size: 12px;'>
        Nota: Los montos en <span style='color: gray;'><strong>gris</strong></span> son exactos (API), los en <span style='color: green;'><strong>verde</strong></span> son estimados.
    </p>
    """,
    unsafe_allow_html=True
    )
    
    if not df_saved_view.empty:
        styled_saved = apply_text_color(df_saved_view)
        ukey_s = f"saved_{len(df_saved_view)}_{st.session_state.get('last_update',0)}"
        edited_s = st.data_editor(
            styled_saved,
            column_config=col_config,
            column_order=ordered,
            hide_index=True,
            use_container_width=True,
            key=ukey_s
        )
        if handle_changes(edited_s, df_saved_view):
            st.session_state.last_update = datetime.now().timestamp()
            st.rerun()
    else:
        st.info("Sin guardados.")

# --- TAB 3 (Detail) ---
with tab_detail:
    if st.session_state.selected_code and st.session_state.selected_code in full_map:
        code = st.session_state.selected_code
        data = full_map[code]
        
        status = "Guardado" if code in saved_ids else ("Nuevo" if code not in history_ids else "Visto")
        st.subheader(data.get("Nombre"))
        st.caption(f"ID: {code} | Estado: {status}")
        
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
