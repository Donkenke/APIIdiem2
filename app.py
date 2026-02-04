import streamlit as st
import pandas as pd
import requests
import urllib3
import json
import sqlite3
import time
import math
import concurrent.futures
import numpy as np
import random
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- CONFIGURATION ---
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
st.set_page_config(page_title="Monitor IDIEM Pro", page_icon="⚡", layout="wide")

# Constants
BASE_URL = "https://api.mercadopublico.cl/servicios/v1/publico"
DB_FILE = "licitaciones_v11_fast_ui.db" 
ITEMS_PER_PAGE = 50 
MAX_WORKERS = 3  # REDUCED from 5 to be more respectful to API
DETAIL_BATCH_SIZE = 20  # NEW: Fetch details in smaller batches
REQUEST_DELAY = 0.2  # INCREASED from 0.1 to reduce load

# --- SMART CATEGORIZATION (Raíces Inteligentes) ---
SMART_CATEGORIES = {
    "Inspección Técnica": ["inspeccion", " ito ", " ito.", "aif", "ait", "atod", "ato ", "supervision"],
    "Ingeniería y Lab": ["geotecn", "mecanica de suelo", "laboratorio", "ensayo", "hormigon", "asfalto", "acero", "estructural", "ingenieria", "geologia", "sondaje", "calicata"],
    "Topografía": ["topograf", "mensura", "fotogramet", "levantamiento", "geodesic", "cartograf"],
    "Sustentabilidad": ["sustentab", "huella de carbono", "climat", "emision", "energetica", "ambiental", "riles", "acustic", "ruido"],
    "Gestión y Forense": ["forense", "peritaje", "reclamacion", "contrato", "bases", "costo", "vibracion"],
    "Arquitectura": ["arquitectura", "diseño", "anteproyecto", "patrimonio", "monumento", "cesfam"],
    "Infraestructura": ["vialidad", "pavimento", "aerodromo", "aeropuerto", "tunel", "puente", "hidraulic", "riego"],
    "Mandantes Clave": ["minvu", "serviu", "mop", "vialidad", "arquitectura", "salud", "hospital", "educacion", "junji"]
}

# --- SCORING RULES (Puntaje de Relevancia) ---
SCORING_RULES = {
    # TIER 1: CORE (10 pts)
    "geotecn": 10, "mecanica de suelo": 10, "calicata": 10, "sondaje": 10,
    "laboratorio": 10, "ensayo": 10, "hormigon": 10, "asfalto": 10,
    "forense": 10, "peritaje": 10,
    # TIER 2: HIGH (6-8 pts)
    "ito ": 8, "inspeccion": 6, "supervision": 6, 
    "topograf": 6, "mensura": 6, "fotogramet": 6,
    "huella de carbono": 8, "sustentab": 7, "eficiencia energetica": 7,
    "acero": 8, "estructural": 6, "sismico": 6,
    # TIER 3: CONTEXT (2 pts)
    "ingenieria": 2, "estudio": 2, "consultoria": 2, "diseño": 2, 
    "proyecto": 1, "obra": 1, "edificacion": 2,
    # PENALIZACIONES (Filtros Negativos)
    "arriendo": -5, "compra de": -2, "suministro": -2, "catering": -10, 
    "aseo": -10, "vigilancia": -10, "transporte": -5, "productora": -10
}

# --- DATABASE ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS marcadores (
        codigo_externo TEXT PRIMARY KEY,
        nombre TEXT,
        organismo TEXT,
        fecha_cierre TEXT,
        url TEXT,
        raw_data TEXT,
        fecha_guardado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS ignorados (
        codigo_externo TEXT PRIMARY KEY,
        fecha_ignorado TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS cache_detalles (
        codigo_externo TEXT PRIMARY KEY,
        json_data TEXT,
        fecha_ingreso TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    # NEW: Cache for daily summaries to avoid re-fetching
    c.execute('''CREATE TABLE IF NOT EXISTS cache_summaries (
        fecha TEXT PRIMARY KEY,
        json_data TEXT,
        fecha_cache TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

# --- DB HELPERS ---
def get_ignored_set():
    try:
        conn = sqlite3.connect(DB_FILE)
        res = set(pd.read_sql("SELECT codigo_externo FROM ignorados", conn)['codigo_externo'])
        conn.close()
        return res
    except: return set()

def ignore_tender(code):
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO ignorados (codigo_externo) VALUES (?)", (code,))
    conn.commit()
    conn.close()

def save_tender(data):
    try:
        clean = data.copy()
        for k in ['Web','Guardar','Ignorar','MontoStr','EstadoTiempo', 'Similitud']: 
            clean.pop(k, None)
        conn = sqlite3.connect(DB_FILE)
        conn.execute("INSERT OR REPLACE INTO marcadores (codigo_externo, nombre, organismo, fecha_cierre, url, raw_data) VALUES (?,?,?,?,?,?)",
                     (clean['CodigoExterno'], clean['Nombre'], clean['Organismo'], str(clean['FechaCierre']), clean['Link'], json.dumps(clean, default=str)))
        conn.commit()
        conn.close()
        return True
    except: return False

def get_saved():
    try:
        conn = sqlite3.connect(DB_FILE)
        df = pd.read_sql("SELECT * FROM marcadores ORDER BY fecha_guardado DESC", conn)
        conn.close()
        return df
    except: return pd.DataFrame()

# NEW: Cache management for summaries
def get_cached_summary(fecha_str):
    """Get cached daily summary if it exists and is recent (< 6 hours old)"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""SELECT json_data, fecha_cache FROM cache_summaries 
                    WHERE fecha = ? 
                    AND datetime(fecha_cache) > datetime('now', '-6 hours')""", (fecha_str,))
        row = c.fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
        return None
    except:
        return None

def save_summary_cache(fecha_str, data):
    """Save daily summary to cache"""
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("INSERT OR REPLACE INTO cache_summaries (fecha, json_data) VALUES (?,?)", 
                    (fecha_str, json.dumps(data)))
        conn.commit()
        conn.close()
    except:
        pass

# --- CACHE & API ---
def get_cached_details(codigos):
    if not codigos: return {}
    conn = sqlite3.connect(DB_FILE)
    placeholders = ','.join(['?']*len(codigos))
    try:
        df = pd.read_sql(f"SELECT codigo_externo, json_data FROM cache_detalles WHERE codigo_externo IN ({placeholders})", conn, params=codigos)
        conn.close()
        return dict(zip(df['codigo_externo'], df['json_data']))
    except: return {}

def save_cache(code, data):
    try:
        conn = sqlite3.connect(DB_FILE)
        conn.execute("INSERT OR REPLACE INTO cache_detalles (codigo_externo, json_data) VALUES (?,?)", (code, json.dumps(data)))
        conn.commit()
        conn.close()
    except: pass

def get_api_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    })
    retry_strategy = Retry(
        total=3, 
        backoff_factor=1, 
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

# OPTIMIZED: Fetch summaries with caching
@st.cache_data(ttl=300) 
def fetch_summaries_raw(start_date, end_date, ticket):
    """
    OPTIMIZATION 1: Use cached summaries when available
    OPTIMIZATION 2: Parallel fetching of uncached dates
    """
    results = []
    errors = []
    delta = (end_date - start_date).days + 1
    
    # Check which dates need fetching
    dates_to_fetch = []
    for i in range(delta):
        d = start_date + timedelta(days=i)
        d_str = d.strftime("%d%m%Y")
        
        # Try to get from cache first
        cached = get_cached_summary(d_str)
        if cached:
            for item in cached:
                item['_fecha_origen'] = d_str
            results.extend(cached)
        else:
            dates_to_fetch.append((d, d_str))
    
    if dates_to_fetch:
        st.info(f"Descargando {len(dates_to_fetch)} días no cacheados...")
        
        # Parallel fetch for uncached dates
        def fetch_day(date_tuple):
            d, d_str = date_tuple
            url = f"{BASE_URL}/licitaciones.json?fecha={d_str}&ticket={ticket}"
            session = get_api_session()
            try:
                r = session.get(url, verify=False, timeout=15)
                if r.status_code == 200:
                    js = r.json()
                    items = js.get('Listado', [])
                    # Save to cache
                    save_summary_cache(d_str, items)
                    for item in items:
                        item['_fecha_origen'] = d_str
                    return items, None
                else:
                    return [], f"Error {r.status_code} en {d_str}"
            except Exception as e:
                return [], f"Fallo conexión en {d_str}: {str(e)}"
        
        # Use ThreadPoolExecutor for parallel fetching
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = [executor.submit(fetch_day, dt) for dt in dates_to_fetch]
            for future in concurrent.futures.as_completed(futures):
                items, error = future.result()
                if items:
                    results.extend(items)
                if error:
                    errors.append(error)
                time.sleep(REQUEST_DELAY)  # Rate limiting
            
    return results, errors

def fetch_detail_worker(args):
    code, ticket = args
    try:
        session = get_api_session() 
        url = f"{BASE_URL}/licitaciones.json?codigo={code}&ticket={ticket}"
        r = session.get(url, verify=False, timeout=15)
        time.sleep(REQUEST_DELAY)  # INCREASED delay
        if r.status_code == 200:
            js = r.json()
            if 'Listado' in js and len(js['Listado']) > 0:
                return code, js['Listado'][0]
        return code, None
    except:
        return code, None

def parse_date(s):
    if not s: return None
    try:
        for fmt in ["%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%Y-%m-%d"]:
            try: return datetime.strptime(s, fmt)
            except: continue
    except: pass
    return None

def matches_keywords(text, category_keywords):
    txt_lower = text.lower()
    for kw in category_keywords:
        if kw.lower() in txt_lower:
            return True, kw
    return False, None

def calculate_relevance_heuristic(results):
    scores = []
    for r in results:
        txt = f"{r.get('Nombre','')} {r.get('Descripcion','')}".lower()
        score = 0.0
        for word, pts in SCORING_RULES.items():
            if word.lower() in txt:
                score += pts
        norm = max(0, min(100, score))
        scores.append(norm / 100.0)
    return scores

def main():
    init_db()
    
    st.title("⚡ Monitor IDIEM Pro - Optimizado")
    st.caption("Versión optimizada con cache inteligente y fetching en paralelo")
    
    t_search, t_res, t_audit, t_sav = st.tabs(["🔎 Búsqueda", "📋 Resultados", "📊 Auditoría", "💾 Guardados"])
    
    with t_search:
        with st.form("search"):
            ticket = st.text_input("🎫 Ticket MercadoPublico", value="", type="password")
            
            col1, col2 = st.columns(2)
            with col1: start = st.date_input("Desde", value=datetime.now() - timedelta(days=7))
            with col2: end = st.date_input("Hasta", value=datetime.now())
            
            cats = st.multiselect("Categorías (Todas si vacío)", list(SMART_CATEGORIES.keys()))
            show_closed = st.checkbox("Incluir licitaciones cerradas", value=False)
            
            submitted = st.form_submit_button("🚀 Buscar", use_container_width=True)
        
        if submitted and ticket:
            audit_logs = []
            
            # 1. Get summaries (now with caching and parallel fetching)
            summaries, errors = fetch_summaries_raw(start, end, ticket)
            if errors: 
                st.warning(f"Errores: {len(errors)}")
                for e in errors[:5]: st.caption(e)
            
            st.info(f"📥 {len(summaries)} licitaciones obtenidas")
            
            # 2. Filter candidates
            search_cats = cats if cats else list(SMART_CATEGORIES.keys())
            candidates = []
            ignored = get_ignored_set()
            
            for item in summaries:
                code = item.get('CodigoExterno')
                if code in ignored:
                    audit_logs.append({"ID": code, "Estado_Audit": "Ignorado", "Motivo": "Lista Negra"})
                    continue
                
                name = str(item.get('Nombre', ''))
                matched = False
                for cat in search_cats:
                    kws = SMART_CATEGORIES[cat]
                    is_match, kw = matches_keywords(name, kws)
                    if is_match:
                        candidates.append({
                            'CodigoExterno': code,
                            'Nombre': name,
                            '_cat': cat,
                            '_kw': kw
                        })
                        audit_logs.append({"ID": code, "Estado_Audit": "Candidato", "Motivo": f"{cat}:{kw}"})
                        matched = True
                        break
                
                if not matched:
                    audit_logs.append({"ID": code, "Estado_Audit": "No Match", "Motivo": "Sin palabras clave"})
            
            st.info(f"🎯 {len(candidates)} candidatos encontrados")
            
            # OPTIMIZATION 3: Smart prioritization - only fetch details for top candidates
            # Sort candidates by likely relevance based on name
            def quick_score(name):
                score = 0
                name_lower = name.lower()
                for word, pts in SCORING_RULES.items():
                    if word in name_lower:
                        score += pts
                return score
            
            candidates_scored = [(c, quick_score(c['Nombre'])) for c in candidates]
            candidates_scored.sort(key=lambda x: x[1], reverse=True)
            
            # OPTIMIZATION 4: Limit detail fetching to top N results
            MAX_DETAILS_TO_FETCH = 200  # Configurable limit
            if len(candidates_scored) > MAX_DETAILS_TO_FETCH:
                st.warning(f"⚠️ Limitando a los {MAX_DETAILS_TO_FETCH} candidatos más relevantes de {len(candidates_scored)} totales")
                candidates = [c for c, _ in candidates_scored[:MAX_DETAILS_TO_FETCH]]
            else:
                candidates = [c for c, _ in candidates_scored]
            
            # 3. Fetch details with caching
            all_candidate_codes = [c['CodigoExterno'] for c in candidates]
            cached_map = get_cached_details(all_candidate_codes)
            codes_needed_for_api = [c for c in all_candidate_codes if c not in cached_map]
            
            # OPTIMIZATION 5: Batched fetching with progress
            if codes_needed_for_api:
                st.info(f"📡 Descargando {len(codes_needed_for_api)} detalles faltantes...")
                pbar = st.progress(0)
                
                # Process in batches
                for batch_start in range(0, len(codes_needed_for_api), DETAIL_BATCH_SIZE):
                    batch_codes = codes_needed_for_api[batch_start:batch_start + DETAIL_BATCH_SIZE]
                    tasks = [(code, ticket) for code in batch_codes]
                    
                    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                        future_to_code = {executor.submit(fetch_detail_worker, task): task[0] for task in tasks}
                        for future in concurrent.futures.as_completed(future_to_code):
                            code_done, detail_data = future.result()
                            if detail_data:
                                save_cache(code_done, detail_data)
                                cached_map[code_done] = json.dumps(detail_data)
                    
                    # Update progress
                    progress = min(1.0, (batch_start + len(batch_codes)) / len(codes_needed_for_api))
                    pbar.progress(progress)
                    
                    # Rate limiting between batches
                    if batch_start + DETAIL_BATCH_SIZE < len(codes_needed_for_api):
                        time.sleep(REQUEST_DELAY * 2)  # Extra delay between batches
                
                pbar.empty()
            
            # 4. Final Processing
            final_list = []
            for cand in candidates:
                code = cand['CodigoExterno']
                detail = None
                if code in cached_map:
                    try: detail = json.loads(cached_map[code])
                    except: pass
                
                if detail:
                    d_cierre = parse_date(detail.get('Fechas', {}).get('FechaCierre'))
                    is_valid = False
                    if show_closed: is_valid = True
                    elif d_cierre and d_cierre >= datetime.now(): is_valid = True
                    
                    if is_valid:
                        row = {
                            "CodigoExterno": code,
                            "Link": f"https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idLicitacion={code}",
                            "Nombre": str(detail.get('Nombre','')).title(),
                            "Organismo": str(detail.get('Comprador',{}).get('NombreOrganismo','')).title(),
                            "Unidad": str(detail.get('Comprador',{}).get('NombreUnidad','')).title(),
                            "FechaPublicacion": parse_date(detail.get('Fechas',{}).get('FechaPublicacion')),
                            "FechaCierre": d_cierre,
                            "Descripcion": detail.get('Descripcion',''),
                            "Categoría": cand['_cat'],
                            "Palabra Clave": cand['_kw'],
                            "EstadoTiempo": "🟢 Vigente" if (d_cierre and d_cierre >= datetime.now()) else "🔴 Cerrada"
                        }
                        if not d_cierre: row["EstadoTiempo"] = "⚠️ Sin Fecha"
                        final_list.append(row)
                        
                        for l in audit_logs:
                            if l['ID'] == code: l['Estado_Audit'], l['Motivo'] = "VISIBLE", "OK"
                    else:
                         for l in audit_logs:
                            if l['ID'] == code: l['Estado_Audit'], l['Motivo'] = "Descartado", "Vencida (Detalle)"
                else:
                     for l in audit_logs:
                         if l['ID'] == code: l['Estado_Audit'], l['Motivo'] = "Error API", "Fallo descarga"

            if final_list:
                scores = calculate_relevance_heuristic(final_list)
                for i, row in enumerate(final_list):
                    row['Similitud'] = scores[i] 

            st.session_state.search_results = pd.DataFrame(final_list)
            st.session_state.audit_data = pd.DataFrame(audit_logs)
            st.session_state.page_number = 1
            st.success(f"✅ Búsqueda completada: {len(final_list)} resultados")

    # RENDERING (Same as original)
    with t_res:
        if 'search_results' in st.session_state and not st.session_state.search_results.empty:
            df = st.session_state.search_results.copy()
            
            # --- GLOBAL SORT ---
            c_sort1, c_sort2 = st.columns([3, 1])
            with c_sort1:
                st.caption(f"Mostrando {len(df)} licitaciones")
            with c_sort2:
                sort_opt = st.selectbox("Ordenar por:", ["Relevancia (Alta)", "Fecha Publicación (Reciente)", "Fecha Cierre (Pronta)"], label_visibility="collapsed")

            if sort_opt == "Relevancia (Alta)":
                if "Similitud" not in df.columns: df["Similitud"] = 0.0
                df = df.sort_values("Similitud", ascending=False)
            elif sort_opt == "Fecha Publicación (Reciente)":
                df = df.sort_values("FechaPublicacion", ascending=False)
            elif sort_opt == "Fecha Cierre (Pronta)":
                df = df.sort_values("FechaCierre", ascending=True)

            # Columns Init
            df["Web"] = df["Link"]
            df["Guardar"] = False
            df["Ignorar"] = False
            
            # Pagination
            total_rows = len(df)
            total_pages = math.ceil(total_rows / ITEMS_PER_PAGE)
            
            # --- COMPACT NAV ---
            col_nav1, col_nav2, col_nav3, col_nav4, col_nav5 = st.columns([4, 1, 3, 1, 4])
            with col_nav2:
                if st.button("◀", key="prev", use_container_width=True) and st.session_state.page_number > 1: 
                    st.session_state.page_number -= 1
            with col_nav3:
                st.markdown(f"<div style='text-align:center; padding-top:5px; font-weight:bold;'>{st.session_state.page_number} / {total_pages}</div>", unsafe_allow_html=True)
            with col_nav4:
                if st.button("▶", key="next", use_container_width=True) and st.session_state.page_number < total_pages: 
                    st.session_state.page_number += 1
            
            idx_start = (st.session_state.page_number - 1) * ITEMS_PER_PAGE
            df_page = df.iloc[idx_start : idx_start + ITEMS_PER_PAGE]
            
            # --- TABLE ---
            edited = st.data_editor(
                df_page,
                column_order=[
                    "Web", "CodigoExterno", "Nombre", 
                    "Organismo", "Unidad", 
                    "EstadoTiempo", "FechaPublicacion", "FechaCierre", 
                    "Categoría", "Palabra Clave", "Ignorar", "Guardar", 
                    "Similitud"
                ],
                column_config={
                    "Web": st.column_config.LinkColumn("🔗", width="small", display_text="🔗"),
                    "CodigoExterno": st.column_config.TextColumn("ID", width="medium"),
                    "Nombre": st.column_config.TextColumn("Nombre Licitación", width="large"),
                    "Organismo": st.column_config.TextColumn("Organismo", width="medium"),
                    "Unidad": st.column_config.TextColumn("Unidad Compra", width="medium"),
                    "Ignorar": st.column_config.CheckboxColumn("🗑️", width="small", default=False),
                    "Guardar": st.column_config.CheckboxColumn("💾", width="small", default=False),
                    "Similitud": st.column_config.ProgressColumn(
                        "Relevancia", format=" ", min_value=0, max_value=1, width="medium"
                    ),
                    "FechaPublicacion": st.column_config.DateColumn("Publicado", format="DD/MM/YY"),
                    "FechaCierre": st.column_config.DateColumn("Cierre", format="DD/MM/YY"),
                },
                hide_index=True,
                height=750,
                key=f"editor_{st.session_state.page_number}"
            )
            
            c_a1, c_a2 = st.columns(2)
            with c_a1:
                if st.button("💾 Guardar Seleccionados", use_container_width=True):
                    to_save = edited[edited["Guardar"] == True]
                    cnt = sum(save_tender(r.to_dict()) for _, r in to_save.iterrows())
                    if cnt: st.toast(f"Guardados: {cnt}", icon="💾")
            with c_a2:
                if st.button("🚫 Ocultar (Lista Negra)", use_container_width=True):
                    to_ignore = edited[edited["Ignorar"] == True]
                    for _, r in to_ignore.iterrows(): ignore_tender(r['CodigoExterno'])
                    if not to_ignore.empty: 
                        st.toast(f"Ocultados: {len(to_ignore)}", icon="🗑️")
                        time.sleep(1); st.rerun()
        else:
            st.info("Sin resultados disponibles.")

    with t_audit:
        if 'audit_data' in st.session_state:
            st.dataframe(st.session_state.audit_data, use_container_width=True)

    with t_sav:
        saved = get_saved()
        if not saved.empty: st.dataframe(saved)
        else: st.info("No hay guardados")

    with st.sidebar:
        st.success("✅ IDIEM Smart Core - OPTIMIZADO")
        st.caption("✨ Con cache inteligente y rate limiting")
        st.divider()
        
        # Show cache stats
        try:
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM cache_summaries")
            summary_cache = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM cache_detalles")
            detail_cache = c.fetchone()[0]
            conn.close()
            
            st.metric("Días en cache", summary_cache)
            st.metric("Detalles en cache", detail_cache)
        except:
            pass
        
        st.divider()
        ign = get_ignored_set()
        if ign:
            if st.button(f"Restaurar {len(ign)} Ocultos"):
                conn = sqlite3.connect(DB_FILE)
                conn.execute("DELETE FROM ignorados")
                conn.commit()
                conn.close()
                st.rerun()

if __name__ == "__main__":
    main()
