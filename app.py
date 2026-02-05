import streamlit as st
import pandas as pd
import json
import os
import re

# --- CONFIGURATION ---
st.set_page_config(page_title="Monitor Licitaciones IDIEM", layout="wide", page_icon="🏗️")

# UTM Value (Feb 2026 approx or current)
UTM_VALUE = 69611 

# Custom CSS for spacing and simple styling
st.markdown("""
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 2rem; }
        .stDataFrame { border: 1px solid #f0f2f6; border-radius: 8px; }
        /* Make tabs look cleaner */
        .stTabs [data-baseweb="tab-list"] { gap: 20px; }
        .stTabs [data-baseweb="tab"] { height: 45px; border-radius: 4px 4px 0 0; }
        .stTabs [aria-selected="true"] { border-top: 3px solid #ff4b4b; }
    </style>
""", unsafe_allow_html=True)

if 'selected_code' not in st.session_state:
    st.session_state.selected_code = None

# ==========================================
# 🧮 HELPER FUNCTIONS
# ==========================================

def clean_money_string(text):
    if not text: return 0
    try:
        clean = re.sub(r'[^\d]', '', str(text))
        if clean: return float(clean)
    except: pass
    return 0

def estimate_monto_from_text(text):
    if not text: return 0, "No informado"
    
    matches = re.findall(r'(\d[\d\.]*)', text)
    numbers = []
    for m in matches:
        try:
            val = int(m.replace(".", ""))
            numbers.append(val)
        except: pass
    
    numbers = sorted(numbers)
    min_utm, max_utm = 0, 0
    text_lower = text.lower()
    
    if len(numbers) >= 2:
        min_utm, max_utm = numbers[0], numbers[1]
    elif len(numbers) == 1:
        val = numbers[0]
        if "inferior" in text_lower or "menor" in text_lower:
            min_utm, max_utm = 0, val
        elif "superior" in text_lower or "mayor" in text_lower:
            min_utm, max_utm = val, val * 3 
        else:
            min_utm, max_utm = 0, val
    else:
        return 0, "Rango no detectado"

    avg_utm = (min_utm + max_utm) / 3
    estimated_clp = avg_utm * UTM_VALUE
    return estimated_clp, f"Est. Rango {min_utm}-{max_utm} UTM"

# ==========================================
# 🛠️ DATA LOADING
# ==========================================
@st.cache_data
def load_data(json_path):
    if not os.path.exists(json_path):
        return pd.DataFrame(), {}
    
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    rows = []
    full_details_map = {}
    
    for item in data:
        code = item.get("CodigoExterno")
        
        # --- MONTO LOGIC ---
        monto_final = 0
        is_estimated = False
        monto_desc = "Monto Real (API)"
        
        # 1. API
        has_val = False
        api_monto = item.get("MontoEstimado")
        try:
            if api_monto is not None:
                m_val = float(api_monto)
                if m_val > 0:
                    monto_final = m_val
                    has_val = True
        except: pass
        
        # 2. Scraped Presupuesto
        if not has_val:
            ext_meta = item.get("ExtendedMetadata", {})
            sec1 = ext_meta.get("Section_1_Características", {})
            presupuesto_str = sec1.get("Presupuesto")
            p_val = clean_money_string(presupuesto_str)
            if p_val > 0:
                monto_final = p_val
                has_val = True
                monto_desc = "Presupuesto (Scraped)"
        
        # 3. Estimation
        if not has_val:
            ext_meta = item.get("ExtendedMetadata", {})
            sec1 = ext_meta.get("Section_1_Características", {})
            tipo_lic = sec1.get("Tipo de Licitación", "")
            if tipo_lic:
                est_val, desc = estimate_monto_from_text(tipo_lic)
                if est_val > 0:
                    monto_final = est_val
                    is_estimated = True
                    monto_desc = desc
        
        # --- BUILD ROW ---
        # Note: We prepare columns specifically for st.column_config
        row = {
            "Codigo": code,
            "Nombre": item.get("Nombre", ""),
            "Organismo": item.get("Comprador", {}).get("NombreOrganismo", ""),
            "Monto": monto_final,
            # We use an icon path or emoji for the status column
            "Tipo_Valor": "🔵" if is_estimated else None, 
            "Monto_Detalle": monto_desc,
            "Publicacion": item.get("Fechas", {}).get("FechaPublicacion", "")[:10] if item.get("Fechas") else "",
            "Cierre": item.get("Fechas", {}).get("FechaCierre", "")[:10] if item.get("Fechas") else "",
            "Categoría": item.get("Match_Category", "-"),
            "Keyword": item.get("Match_Keyword", "-"),
            "URL_Ficha": item.get("URL_Publica"),
            "Descripcion": item.get("Descripcion", "")
        }
        rows.append(row)
        full_details_map[code] = item 

    return pd.DataFrame(rows), full_details_map

# Load Data
JSON_FILE = "FINAL_PRODUCTION_DATA.json" 
df, full_map = load_data(JSON_FILE)

# ==========================================
# 🖥️ UI LAYOUT
# ==========================================

with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/121px-Python-logo-notext.svg.png", width=40)
    st.title("Panel de Control")
    st.metric("Licitaciones", len(df))
    st.metric("Valor UTM", f"${UTM_VALUE:,.0f}")
    
    if st.button("🔄 Recargar", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    if st.session_state.selected_code:
        st.info(f"Selección: {st.session_state.selected_code}")
        if st.button("Limpiar Selección", use_container_width=True):
            st.session_state.selected_code = None
            st.rerun()

st.title("🏗️ Monitor Licitaciones IDIEM")

tab_list, tab_detail = st.tabs(["📋 Listado General", "📄 Ficha Técnica"])

# --- TAB 1: MODERN TABLE ---
with tab_list:
    if not df.empty:
        # Note aligned to right
        _, c_note = st.columns([2, 3])
        with c_note:
            st.markdown(
                """<div style="text-align: right; font-size: 0.85em; color: #666; margin-bottom: 5px;">
                Nota: El ícono 🔵 indica que el <b>Monto</b> es una estimación basada en rango UTM.
                </div>""", 
                unsafe_allow_html=True
            )

        # Prepare DataFrame for Display
        # We assume specific column order
        cols_order = [
            "Codigo", 
            "URL_Ficha", 
            "Nombre", 
            "Publicacion", 
            "Cierre", 
            "Categoría", 
            "Keyword", 
            "Organismo", 
            "Tipo_Valor", # The Icon Column
            "Monto"
        ]
        
        # Filter existing columns
        df_display = df[[c for c in cols_order if c in df.columns]].copy()

        # CONFIGURATION for st.dataframe
        column_config = {
            "Codigo": st.column_config.TextColumn("ID", width="small", help="Código Externo"),
            "URL_Ficha": st.column_config.LinkColumn("Web", display_text="🔗", width="small"),
            "Nombre": st.column_config.TextColumn("Nombre Licitación", width="large"),
            "Publicacion": st.column_config.DateColumn("Publicación", format="DD/MM/YYYY"),
            "Cierre": st.column_config.DateColumn("Cierre", format="DD/MM/YYYY"),
            "Categoría": st.column_config.TextColumn("Categoría", width="medium"),
            "Keyword": st.column_config.TextColumn("Match", width="small"),
            "Organismo": st.column_config.TextColumn("Organismo", width="medium"),
            "Tipo_Valor": st.column_config.TextColumn("Est.", width="small", help="🔵 = Estimado por rango UTM"),
            "Monto": st.column_config.NumberColumn(
                "Monto (CLP)", 
                format="$%d", 
                width="medium"
            )
        }

        # RENDER TABLE
        # on_select="rerun" enables the interactive selection
        event = st.dataframe(
            df_display,
            column_config=column_config,
            use_container_width=True,
            hide_index=True,
            on_select="rerun", 
            selection_mode="single-row",
            height=600
        )

        # SELECTION LOGIC
        if event.selection.rows:
            # Get the index of the selected row
            idx = event.selection.rows[0]
            # Retrieve the Code from the displayed dataframe
            code = df_display.iloc[idx]["Codigo"]
            
            if st.session_state.selected_code != code:
                st.session_state.selected_code = code
                st.rerun()

    else:
        st.info("No hay datos disponibles.")

# --- TAB 2: DETAIL VIEW ---
with tab_detail:
    if st.session_state.selected_code and st.session_state.selected_code in full_map:
        data = full_map[st.session_state.selected_code]
        ext_meta = data.get("ExtendedMetadata", {})
        sec1 = ext_meta.get("Section_1_Características", {})
        
        # --- HEADER ---
        c1, c2 = st.columns([4, 1])
        with c1:
            st.subheader(f"{data.get('Nombre')}")
            st.caption(f"ID: {data.get('CodigoExterno')} | Estado: {data.get('Estado')}")
        with c2:
            st.link_button("🌐 MercadoPúblico", data.get('URL_Publica') or "#", use_container_width=True)

        st.divider()
        
        # --- SECTION 1 ---
        st.markdown("#### 📌 Características")
        if sec1:
            mop_presupuesto = sec1.get("Presupuesto")
            mop_financ = sec1.get("FuenteFinanciamiento")
            
            k1, k2, k3 = st.columns(3)
            with k1:
                st.write(f"**Tipo:** {sec1.get('Tipo de Licitación', 'N/A')}")
                if mop_presupuesto:
                    st.write(f"**Presupuesto (Ficha):** :green[{mop_presupuesto}]")
            with k2:
                st.write(f"**Etapas:** {sec1.get('Etapas del proceso', 'N/A')}")
                if mop_financ:
                    st.write(f"**Financiamiento:** {mop_financ}")
            with k3:
                st.info(f"**Estado:** {sec1.get('Estado', 'N/A')}")
                if sec1.get("TipoGastos"):
                    st.write(f"**Gastos:** {sec1.get('TipoGastos')}")
        else:
            st.warning("Metadata extendida no disponible.")

        st.divider()

        # --- GENERAL INFO ---
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("###### 🏢 Organismo")
            comp = data.get('Comprador', {})
            st.write(f"**Entidad:** {comp.get('NombreOrganismo')}")
            st.write(f"**Unidad:** {comp.get('NombreUnidad')}")
            st.write(f"**Ubicación:** {comp.get('RegionUnidad')}")
        
        with col_b:
            st.markdown("###### 💰 Negocio")
            # Logic to explain the amount shown
            m_api = data.get('MontoEstimado')
            m_scrap = sec1.get("Presupuesto") if sec1 else None
            
            if m_api and float(m_api) > 0:
                 st.write(f"**Monto API:** {m_api}")
            elif m_scrap:
                 st.write(f"**Monto Scraped:** {m_scrap}")
            else:
                 est_val, desc = estimate_monto_from_text(sec1.get('Tipo de Licitación', ''))
                 if est_val > 0:
                     st.markdown(f"**Monto Estimado:** :blue[${est_val:,.0f}]")
                     st.caption(f"({desc})")
                 else:
                     st.write("**Monto:** No informado")

            st.write(f"**Cierre:** {data.get('Fechas', {}).get('FechaCierre', '')[:10]}")

        st.divider()
        st.markdown("##### 📝 Descripción")
        st.info(data.get('Descripcion', 'Sin descripción'))

        # --- ITEMS TABLE (Native Streamlit) ---
        st.divider()
        st.markdown("###### 📦 Ítems")
        items_list = data.get('Items', {}).get('Listado', [])
        if not items_list and 'DetalleArticulos' in data:
            items_list = data['DetalleArticulos']
            
        if items_list:
            df_items = pd.json_normalize(items_list)
            cols_wanted = ['NombreProducto', 'Descripcion', 'Cantidad', 'UnidadMedida']
            cols_present = [c for c in cols_wanted if c in df_items.columns]
            st.dataframe(df_items[cols_present], use_container_width=True, hide_index=True)
        else:
            st.warning("No hay detalle de ítems.")

    else:
        st.markdown("""<div style="text-align: center; padding: 50px; color: #666;">
            <h3>👈 Selecciona una licitación en el listado</h3></div>""", unsafe_allow_html=True)
