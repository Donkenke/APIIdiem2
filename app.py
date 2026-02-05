import streamlit as st
import pandas as pd
import json
import os
import re
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

# --- CONFIGURATION ---
st.set_page_config(page_title="Monitor Licitaciones IDIEM", layout="wide", page_icon="🏗️")

# UTM Value (Feb 2026 approx or current)
UTM_VALUE = 69611 

# Custom CSS
st.markdown("""
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 2rem; }
        .stDataFrame { border: 1px solid #e0e0e0; border-radius: 5px; }
        .stTabs [data-baseweb="tab-list"] { gap: 24px; }
        .stTabs [data-baseweb="tab"] { height: 50px; background-color: #f0f2f6; border-radius: 4px 4px 0 0; }
        .stTabs [aria-selected="true"] { background-color: #ffffff; border-top: 2px solid #ff4b4b; }
    </style>
""", unsafe_allow_html=True)

if 'selected_code' not in st.session_state:
    st.session_state.selected_code = None

# ==========================================
# 🧮 HELPER FUNCTIONS
# ==========================================

def clean_money_string(text):
    """
    Cleans strings like '180.677.462' or '$ 500' into a float.
    """
    if not text: return 0
    try:
        # Remove everything except digits
        clean = re.sub(r'[^\d]', '', str(text))
        if clean:
            return float(clean)
    except: pass
    return 0

def estimate_monto_from_text(text):
    """
    Parses strings like "igual o superior a 100 UTM e inferior a 1.000 UTM"
    Returns: (Estimated Amount in CLP, Description)
    """
    if not text:
        return 0, "No informado"

    matches = re.findall(r'(\d[\d\.]*)', text)
    numbers = []
    for m in matches:
        try:
            val = int(m.replace(".", ""))
            numbers.append(val)
        except: pass
    
    numbers = sorted(numbers)
    min_utm = 0
    max_utm = 0
    text_lower = text.lower()
    
    if len(numbers) >= 2:
        min_utm = numbers[0]
        max_utm = numbers[1]
    elif len(numbers) == 1:
        val = numbers[0]
        if "inferior" in text_lower or "menor" in text_lower:
            min_utm = 0
            max_utm = val
        elif "superior" in text_lower or "mayor" in text_lower:
            min_utm = val
            max_utm = val * 3 
        else:
            min_utm = 0
            max_utm = val
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
        
        # --- MONTO PRIORITY LOGIC ---
        monto_final = 0
        is_estimated = False
        monto_desc = "Monto Real (API)"
        
        # 1. Try API "MontoEstimado"
        has_val = False
        api_monto = item.get("MontoEstimado")
        try:
            if api_monto is not None:
                m_val = float(api_monto)
                if m_val > 0:
                    monto_final = m_val
                    has_val = True
        except: pass
        
        # 2. Try Scraped "Presupuesto" (New MOP Field)
        # This is considered a "Real" value, so is_estimated = False
        if not has_val:
            ext_meta = item.get("ExtendedMetadata", {})
            sec1 = ext_meta.get("Section_1_Características", {})
            presupuesto_str = sec1.get("Presupuesto")
            
            p_val = clean_money_string(presupuesto_str)
            if p_val > 0:
                monto_final = p_val
                has_val = True
                monto_desc = "Presupuesto (Scraped)"
        
        # 3. Try Estimation (Fallback)
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
        row = {
            "Codigo": code,
            "Nombre": item.get("Nombre", ""),
            "Organismo": item.get("Comprador", {}).get("NombreOrganismo", ""),
            "Monto": monto_final,
            "Es_Estimado": is_estimated,
            "Monto_Detalle": monto_desc,
            "Publicacion": item.get("Fechas", {}).get("FechaPublicacion", "")[:10] if item.get("Fechas") else "",
            "Cierre": item.get("Fechas", {}).get("FechaCierre", "")[:10] if item.get("Fechas") else "",
            "Categoría IDIEM": item.get("Match_Category", "-"),
            "Keyword": item.get("Match_Keyword", "-"),
            "URL_Ficha": item.get("URL_Publica"),
            "URL_Docs": item.get("URL_Documentos_Portal"),
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

# Sidebar
with st.sidebar:
    st.image("https://upload.wikimedia.org/wikipedia/commons/thumb/c/c3/Python-logo-notext.svg/121px-Python-logo-notext.svg.png", width=40)
    st.title("🎛️ Panel de Control")
    st.metric("Licitaciones", len(df))
    st.metric("Valor UTM", f"${UTM_VALUE:,.0f}".replace(",", "."))
    
    if st.button("🔄 Recargar", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    if st.session_state.selected_code:
        st.info(f"Viendo: {st.session_state.selected_code}")
        if st.button("🔙 Volver a Tabla"):
            st.session_state.selected_code = None
            st.rerun()

st.title("🏗️ Monitor Licitaciones IDIEM")

tab_list, tab_detail = st.tabs(["✅ Licitaciones", "📄 Ficha Técnica"])

# --- TAB 1: TABLE ---
with tab_list:
    if not df.empty:
        df_display = df.copy()
        df_display['Nombre_Display'] = df_display['Nombre'].apply(lambda x: x[:85] + '...' if len(x) > 85 else x)
        
        # --- SIMPLE NOTE (Right Aligned) ---
        _, c_note = st.columns([2, 3])
        with c_note:
            st.markdown(
                """<div style="text-align: right; font-size: 0.85em; color: #555; margin-bottom: 5px;">
                Nota: Los valores en <span style="color:#1E90FF; font-weight:bold">azul</span> son estimación basadas en el intervalo de valor UTM definidos en la licitación
                </div>""", 
                unsafe_allow_html=True
            )

        display_cols = [
            'Codigo', 'URL_Ficha', 'Nombre_Display', 'Organismo','Publicacion', 'Cierre',
            'Categoría IDIEM', 'Keyword', 'Monto',
            'Nombre', 'Descripcion', 'Es_Estimado'
        ]
        df_display = df_display[[c for c in display_cols if c in df_display.columns]]

        gb = GridOptionsBuilder.from_dataframe(df_display)
        gb.configure_pagination(paginationAutoPageSize=False, paginationPageSize=100)
        gb.configure_selection('single', use_checkbox=True)
        
        gb.configure_column("Codigo", header_name="ID", width=110, pinned="left")
        
        link_renderer = JsCode("""
            class UrlCellRenderer {
              init(params) {
                this.eGui = document.createElement('a');
                this.eGui.innerHTML = '🔗';
                this.eGui.setAttribute('href', params.value);
                this.eGui.setAttribute('target', '_blank');
                this.eGui.style.textDecoration = 'none';
                this.eGui.style.fontSize = '1.3em';
                this.eGui.style.display = 'block';
                this.eGui.style.textAlign = 'center';
              }
              getGui() { return this.eGui; }
            }
        """)
        gb.configure_column("URL_Ficha", header_name="Web", cellRenderer=link_renderer, width=60, pinned="left")
        
        gb.configure_column("Nombre_Display", header_name="Nombre Licitación", width=400, tooltipField="Nombre")
        gb.configure_column("Publicacion", width=110)
        gb.configure_column("Cierre", width=110)
        gb.configure_column("Categoría IDIEM", width=180)
        gb.configure_column("Keyword", header_name="Match", width=150)
        gb.configure_column("Organismo", width=200)

        # Monto Formatting
        monto_style_jscode = JsCode("""
            function(params) {
                if (params.data.Es_Estimado === true) {
                    return {'color': '#1E90FF', 'font-weight': 'bold'};
                }
                return {'color': 'black'};
            }
        """)
        
        gb.configure_column("Monto", 
                            type=["numericColumn", "numberColumnFilter"], 
                            valueFormatter="x.toLocaleString('es-CL', {style: 'currency', currency: 'CLP'})", 
                            cellStyle=monto_style_jscode,
                            width=130)
        
        for col in ["Nombre", "Descripcion", "Es_Estimado"]:
            gb.configure_column(col, hide=True)

        grid_response = AgGrid(
            df_display,
            gridOptions=gb.build(),
            enable_enterprise_modules=False,
            allow_unsafe_jscode=True,
            update_mode="SELECTION_CHANGED",
            height=800,
            theme='streamlit'
        )
        
        selected = grid_response['selected_rows']
        has_selection = False
        if isinstance(selected, list): has_selection = len(selected) > 0
        elif isinstance(selected, pd.DataFrame): has_selection = not selected.empty

        if has_selection:
            row = selected[0] if isinstance(selected, list) else selected.iloc[0]
            if st.session_state.selected_code != row['Codigo']:
                st.session_state.selected_code = row['Codigo']
                st.rerun()

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
            st.link_button("🌐 MercadoPúblico", data.get('URL_Publica'), use_container_width=True)

        st.divider()
        
        # --- SECTION 1: CARACTERÍSTICAS (Including Repair Data) ---
        st.markdown("#### 📌 Características de la Licitación")
        
        if sec1:
            # Check for MOP Fields
            mop_presupuesto = sec1.get("Presupuesto")
            mop_financ = sec1.get("FuenteFinanciamiento")
            
            k1, k2, k3 = st.columns(3)
            with k1:
                st.write(f"**Tipo:** {sec1.get('Tipo de Licitación', 'N/A')}")
                st.write(f"**Moneda:** {sec1.get('Moneda', 'N/A')}")
                if mop_presupuesto:
                    st.write(f"**Presupuesto (Ficha):** :green[{mop_presupuesto}]")
            with k2:
                st.write(f"**Etapas:** {sec1.get('Etapas del proceso', 'N/A')}")
                st.write(f"**Toma Razón:** {sec1.get('Toma de Razón', 'N/A')}")
                if mop_financ:
                    st.write(f"**Financiamiento:** {mop_financ}")
            with k3:
                st.info(f"**Estado:** {sec1.get('Estado', 'N/A')}")
                if sec1.get("TipoGastos"):
                    st.write(f"**Gastos:** {sec1.get('TipoGastos')}")
        else:
            st.warning("No se pudo extraer la Sección 1.")

        st.divider()

        # --- STANDARD DATA ---
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("###### 🏢 Organismo")
            comp = data.get('Comprador', {})
            st.write(f"**Entidad:** {comp.get('NombreOrganismo')}")
            st.write(f"**Unidad:** {comp.get('NombreUnidad')}")
            st.write(f"**Region:** {comp.get('RegionUnidad')}")
        
        with col_b:
            st.markdown("###### 💰 Negocio y Fechas")
            
            # Smart Monto Display
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

            st.write(f"**Publicación:** {data.get('Fechas', {}).get('FechaPublicacion', '')[:10]}")
            st.write(f"**Cierre:** {data.get('Fechas', {}).get('FechaCierre', '')[:10]}")

        st.divider()
        st.markdown("##### 📝 Descripción")
        st.info(data.get('Descripcion', 'Sin descripción'))

        st.divider()
        st.markdown("###### 📦 Ítems")
        items_list = data.get('Items', {}).get('Listado', [])
        if not items_list and 'DetalleArticulos' in data:
            items_list = data['DetalleArticulos']
            
        if items_list:
            df_items = pd.json_normalize(items_list)
            cols_wanted = ['NombreProducto', 'Descripcion', 'Cantidad', 'UnidadMedida']
            cols_present = [c for c in cols_wanted if c in df_items.columns]
            st.dataframe(df_items[cols_present], use_container_width=True)
        else:
            st.warning("No hay detalle de ítems.")

    else:
        st.markdown("""<div style="text-align: center; padding: 50px; color: #666;">
            <h3>👈 Selecciona una licitación</h3></div>""", unsafe_allow_html=True)
