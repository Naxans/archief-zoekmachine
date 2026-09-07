import io
import time
import string
import logging
import warnings
import json
import re
import gc
import math
from string import Template
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google import genai
from google.genai import types

# ------------------------------------------------------------------------------
# APP VERSIEBEHEER
# ------------------------------------------------------------------------------
APP_VERSION = "v1.6.0 (Direct Drive File Analysis & Multimodal Gemini)"
APP_DATE = "2026"

logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

def natuurlijke_sortering(item):
    tekst = item.get('naam', '') if isinstance(item, dict) else str(item)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', tekst)]

def normaliseer_tekst(tekst):
    """Zet tekst om naar lowercase en vangt spellingsvariaties op."""
    if not tekst:
        return ""
    tekst = str(tekst).lower()
    tekst = tekst.replace('ij', 'y')
    return tekst

# ------------------------------------------------------------------------------
# 1. AUTHENTICATIE VIA STREAMLIT SECRETS
# ------------------------------------------------------------------------------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

@st.cache_resource
def init_services():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=SCOPES
    )
    gc_drive = gspread.authorize(creds)
    drive_service = build('drive', 'v3', credentials=creds)
    ai_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    return gc_drive, drive_service, ai_client

try:
    gc_drive, drive_service, ai_client = init_services()
except Exception as e:
    st.error(f"Fout bij verbinden met Google/Gemini diensten: {e}")
    st.stop()

# ------------------------------------------------------------------------------
# 2. CONFIGURATIE & MODEL SELECTIE
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

def bepaal_werkend_model(client):
    kandidaten = ['gemini-flash-latest', 'gemini-flash-lite-latest']
    for model_naam in kandidaten:
        try:
            client.models.generate_content(model=model_naam, contents="ping")
            return model_naam
        except Exception:
            continue
    return 'gemini-flash-latest'

MODEL_NAAM = bepaal_werkend_model(ai_client)

def genereer_met_retry(client, model, contents, max_retries=4):
    for poging in range(max_retries):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            err_msg = str(e)
            if "503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if poging < max_retries - 1:
                    wachttijd = 15 * (poging + 1)
                    st.info(f"⏳ Gemini API-limiet bereikt. Pauze van {wachttijd} seconden...")
                    time.sleep(wachttijd)
                    continue
                else:
                    st.error("⚠️ De limiet voor de Gemini API is tijdelijk bereikt. Wacht 1-2 minuten.")
            raise e

# Session state variabelen
if "actieve_chat" not in st.session_state:
    st.session_state.actieve_chat = None
if "chat_historie" not in st.session_state:
    st.session_state.chat_historie = []
if "blader_paginas" not in st.session_state:
    st.session_state.blader_paginas = []
if "gestopt" not in st.session_state:
    st.session_state.gestopt = False
if "start_zoekopdracht" not in st.session_state:
    st.session_state.start_zoekopdracht = False
if "geselecteerde_doc_ids" not in st.session_state:
    st.session_state.geselecteerde_doc_ids = []
if "huidige_vraag" not in st.session_state:
    st.session_state.huidige_vraag = ""
if "specifieke_termen" not in st.session_state:
    st.session_state.specifieke_termen = []
if "generieke_termen" not in st.session_state:
    st.session_state.generieke_termen = []
if "harde_naam_targets" not in st.session_state:
    st.session_state.harde_naam_targets = []
if "genegeerde_ruis" not in st.session_state:
    st.session_state.genegeerde_ruis = []

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE
# ------------------------------------------------------------------------------
st.set_page_config(page_title="RBC Archief zoekmachine", page_icon="🔍", layout="wide")

col_title, col_ver = st.columns([4, 1])
with col_title:
    st.title("🔍 RBC Archief zoekmachine")
with col_ver:
    st.caption(f"**Versie:** `{APP_VERSION}` ({APP_DATE})")

if MODEL_NAAM:
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: wat weet je over een royal record radio model vedette?',
        height=100
    )
with col2:
    max_dossiers = st.slider("Max dossiers (Document_ID's):", min_value=5, max_value=50, value=20, step=5)

btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

if submit_button:
    st.session_state.blader_paginas = []
    st.session_state.chat_historie = []
    st.session_state.actieve_chat = None
    st.session_state.geselecteerde_doc_ids = []
    st.session_state.specifieke_termen = []
    st.session_state.generieke_termen = []
    st.session_state.harde_naam_targets = []
    st.session_state.genegeerde_ruis = []
    st.session_state.huidige_vraag = onderzoeksvraag
    gc.collect()

    st.session_state.gestopt = False
    st.session_state.start_zoekopdracht = True
    st.rerun()

if stop_button:
    st.session_state.gestopt = True
    st.session_state.start_zoekopdracht = False
    st.session_state.blader_paginas = []
    gc.collect()
    st.warning("⚠️ Onderzoek geannuleerd.")
    st.stop()

# ------------------------------------------------------------------------------
# 4. QUERY ONTLEDING & DYNAMISCHE SCORING
# ------------------------------------------------------------------------------
if st.session_state.start_zoekopdracht:
    if not st.session_state.huidige_vraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
        st.session_state.start_zoekopdracht = False
    else:
        with st.spinner("Stap 1/3: Inhoudsopgave scannen..."):
            try:
                sh = gc_drive.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.session_state.start_zoekopdracht = False
                st.stop()

        with st.spinner("Stap 1b/3: Dynamische AI-ontleding van de vraag (AI Query Expansion)..."):
            vraag_orig = st.session_state.huidige_vraag

            prompt_extraction = f"""
Jij bent een intelligente zoekarchivaris voor een Belgisch/Nederlands archief. Ontleed de onderstaande zoekvraag van een gebruiker.

GEBRUIKERSVRAAG: "{vraag_orig}"

TAAK:
1. "personen": Extraheer persoonsnamen EN genereer bekende spellingvariaties voor voornamen/achternamen (bijv. "emile" -> ["emile", "emiel"], "mathieu" -> ["mathieu", "matthieu"]).
2. "specifieke_termen": Extraheer uitsluitend de MEEST SPECIFIEKE en UNIEKE identificatierestanten die het exacte onderwerp bepalen. Dit kan een specifiek model, typenummer, dossiernummer, unieke straatnaam of zeldzaam trefwoord zijn. (Bijv. bij "royal record radio model vedette" is dit uitsluitend ["vedette"]).
3. "generieke_termen": Extraheer meer algemene aanduidingen, merknamen, vakgebieden of algemene categorieën die de bredere context beschrijven. (Bijv. ["royal record", "radio", "toestel"]).
4. "ruis_genegeerd": Grammaticale ruis, vraagwoorden en algemene aanduidingen (zoals "wat", "weet", "je", "over", "een", "model", "type", "foto", "document").

Geef UITSLUITEND een geldig JSON-object terug:
{{
  "personen": [],
  "specifieke_termen": ["vedette"],
  "generieke_termen": ["royal record", "radio"],
  "ruis_genegeerd": ["wat", "weet", "je", "over", "een", "model"]
}}
"""
            extracted_data = None

            try:
                res = genereer_met_retry(ai_client, MODEL_NAAM, prompt_extraction)
                json_match = re.search(r'\{.*\}', res.text, re.DOTALL)
                if json_match:
                    extracted_data = json.loads(json_match.group(0))
            except Exception as e:
                st.error(f"⛔ **API-Limiet of Netwerkfout bij AI Query-ontleding:**\n\n{e}\n\n*De zoekopdracht is gestopt om te voorkomen dat er willekeurige documenten en foute analyses worden gegenereerd. Probeer het over 1 minuut opnieuw.*")
                st.session_state.start_zoekopdracht = False
                st.stop()

            if not extracted_data:
                st.error("⛔ **AI Query-ontleding kon geen geldige data structureren.** Zoekopdracht geannuleerd.")
                st.session_state.start_zoekopdracht = False
                st.stop()

            harde_namen = [normaliseer_tekst(p) for p in extracted_data.get("personen", []) if len(p) >= 2]
            
            if 'emile' in harde_namen and 'emiel' not in harde_namen:
                harde_namen.append('emiel')
            elif 'emiel' in harde_namen and 'emile' not in harde_namen:
                harde_namen.append('emile')

            spec_termen = [normaliseer_tekst(s) for s in extracted_data.get("specifieke_termen", []) if len(s) >= 2]
            gen_termen = [normaliseer_tekst(g) for g in extracted_data.get("generieke_termen", []) if len(g) >= 2]
            ruis = [normaliseer_tekst(r) for r in extracted_data.get("ruis_genegeerd", [])]

            st.session_state.harde_naam_targets = list(set(harde_namen))
            st.session_state.specifieke_termen = list(set(spec_termen))
            st.session_state.generieke_termen = list(set(gen_termen))
            st.session_state.genegeerde_ruis = list(set(ruis))

        with st.spinner("Stap 2/3: Archiefstukken & PDF's matchen op inhoud..."):
            dossier_scores = {}

            specifieke_termen = st.session_state.specifieke_termen
            generieke_termen = st.session_state.generieke_termen
            harde_namen = st.session_state.harde_naam_targets

            for row in data:
                b_naam = str(row.get('Bestandsnaam', '')).strip()
                doc_id = str(row.get('Document_ID', '')).strip()
                if not doc_id:
                    doc_id = f"SINGLE_{b_naam}"

                pers = normaliseer_tekst(row.get('Genoemde Personen') or row.get('Genoemde personen') or '')
                ond = normaliseer_tekst(row.get('Onderwerp (NL)') or row.get('Onderwerp') or '')
                inhoud = normaliseer_tekst(row.get('Inhoud & Cijfers (NL)') or row.get('Inhoud & cijfers') or row.get('Inhoud') or '')
                
                b_naam_norm = normaliseer_tekst(b_naam)

                score = 0
                aantal_specifieke_matches = 0
                heeft_specifieke_match = False

                for hn in harde_namen:
                    if hn in b_naam_norm:
                        score += 20000
                        heeft_specifieke_match = True
                    elif hn in pers:
                        score += 8000
                        heeft_specifieke_match = True
                    elif hn in ond or hn in inhoud:
                        score += 2000

                for st_term in specifieke_termen:
                    if st_term in b_naam_norm:
                        score += 25000
                        heeft_specifieke_match = True
                        aantal_specifieke_matches += 1
                    elif st_term in ond or st_term in inhoud:
                        score += 6000
                        heeft_specifieke_match = True
                        aantal_specifieke_matches += 1

                for gt_term in generieke_termen:
                    if gt_term in b_naam_norm:
                        score += 1500
                    elif gt_term in ond or gt_term in inhoud:
                        score += 400

                if (specifieke_termen or harde_namen) and not heeft_specifieke_match:
                    score *= 0.05

                if aantal_specifieke_matches > 1:
                    score *= (1 + (aantal_specifieke_matches * 0.5))

                if score > 0:
                    dossier_scores[doc_id] = dossier_scores.get(doc_id, 0) + score

            gesorteerde_dossiers = [d_id for d_id, sc in sorted(dossier_scores.items(), key=lambda x: x[1], reverse=True)]

            if not gesorteerde_dossiers:
                if specifieke_termen or harde_namen or generieke_termen:
                    st.warning("⚠️ Geen archiefstukken gevonden die overeenkomen met de opgegeven zoektermen.")
                    st.session_state.start_zoekopdracht = False
                    st.stop()
                else:
                    gesorteerde_dossiers = list(set(str(row.get('Document_ID', '')).strip() or f"SINGLE_{str(row.get('Bestandsnaam', '')).strip()}" for row in data))

            st.session_state.geselecteerde_doc_ids = gesorteerde_dossiers[:max_dossiers]

        with st.spinner("Stap 2b/3: Bestanden ophalen uit Google Drive..."):
            sheet_dossier_data = []
            gezochte_bestanden = []
            
            for row in data:
                b_naam = str(row.get('Bestandsnaam', '')).strip()
                doc_id = str(row.get('Document_ID', '')).strip()
                if not doc_id:
                    doc_id = f"SINGLE_{b_naam}"

                if doc_id in st.session_state.geselecteerde_doc_ids:
                    sheet_dossier_data.append(row)
                    if b_naam:
                        schoon_naam = b_naam.split('/')[-1]
                        gezochte_bestanden.append((doc_id, schoon_naam))

            blader_lijst = []
            if gezochte_bestanden:
                batch_size = 40
                drive_map = {}
                unieke_zoeknamen = list(set([naam for _, naam in gezochte_bestanden]))

                for i in range(0, len(unieke_zoeknamen), batch_size):
                    batch = unieke_zoeknamen[i:i + batch_size]
                    namen_query = " or ".join([f"name = '{naam}'" for naam in batch])
                    query = f"({namen_query}) and trashed = false"
                    
                    try:
                        res = drive_service.files().list(
                            q=query, 
                            fields='files(id, name, mimeType)',
                            pageSize=1000
                        ).execute().get('files', [])
                        for f in res:
                            drive_map[f['name'].lower()] = f
                    except Exception:
                        pass
                
                for doc_id, b_schoon in gezochte_bestanden:
                    b_key = b_schoon.lower()
                    if b_key in drive_map:
                        f = drive_map[b_key]
                        if not any(item['id'] == f['id'] for item in blader_lijst):
                            blader_lijst.append({
                                "doc_id": doc_id, 
                                "naam": f['name'], 
                                "id": f['id'], 
                                "mime": f['mimeType']
                            })

            st.session_state.blader_paginas = blader_lijst
            st.session_state.sheet_dossier_data = sheet_dossier_data
            st.session_state.start_zoekopdracht = False
            st.rerun()

# ------------------------------------------------------------------------------
# 5. WEERGAVE VAN DE TEGELS (CENTERED ZOOM & PAN VIEWER)
# ------------------------------------------------------------------------------
if st.session_state.blader_paginas:
    st.divider()
    
    with st.expander("💡 Bekijk de slimme AI Query-analyse & Genegeerde Ruis"):
        st.markdown(f"**Originele vraag:** `{st.session_state.huidige_vraag}`")
        if st.session_state.harde_naam_targets:
            st.markdown(f"**Geëxtraheerde personen:** `{', '.join(st.session_state.harde_naam_targets)}`")
        if st.session_state.specifieke_termen:
            st.markdown(f"**Unieke / Specifieke kernbegrippen (Hoge score):** `{', '.join(st.session_state.specifieke_termen)}`")
        if st.session_state.generieke_termen:
            st.markdown(f"**Generieke contextbegrippen:** `{', '.join(st.session_state.generieke_termen)}`")
        if st.session_state.genegeerde_ruis:
            st.markdown(f"**🚫 Automatisch genegeerde ruis:** `{', '.join(st.session_state.genegeerde_ruis)}`")

    dossiers_dict = {}
    for p in st.session_state.blader_paginas:
        d_id = p.get("doc_id", "Dossier_Onbekend")
        if d_id not in dossiers_dict:
            dossiers_dict[d_id] = []
        dossiers_dict[d_id].append(p)

    for d_id in dossiers_dict:
        dossiers_dict[d_id].sort(key=natuurlijke_sortering)

    tegel_items = []
    volgorde_ids = st.session_state.geselecteerde_doc_ids

    for d_id in volgorde_ids:
        if d_id in dossiers_dict and dossiers_dict[d_id]:
            pagina_lijst = dossiers_dict[d_id]
            eerste_pagina = pagina_lijst[0].copy()
            aantal_pags = len(pagina_lijst)
            eerste_pagina["display_label"] = f"{d_id} ({aantal_pags} pag.)" if aantal_pags > 1 else d_id
            tegel_items.append(eerste_pagina)

    st.subheader(f"🖼️ Geselecteerde Archiefdocumenten ({len(tegel_items)} dossiers • {len(st.session_state.blader_paginas)} bestanden)")
    st.caption("Klik op een tegel om het document te bekijken.")

    html_template = Template("""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body { margin: 0; padding: 5px 0; font-family: sans-serif; background: transparent; }
            .grid-container { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; width: 100%; }
            .tile { background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: transform 0.2s; display: flex; flex-direction: column; align-items: center; overflow: hidden; }
            .tile:hover { transform: translateY(-3px); border-color: #1a73e8; }
            .img-container { width: 100%; height: 180px; background-color: #f5f5f5; display: flex; align-items: center; justify-content: center; overflow: hidden; }
            .img-container img { width: 100%; height: 100%; object-fit: cover; }
            .tile-caption { padding: 10px 8px; font-size: 12px; font-weight: 600; color: #202124; text-align: center; width: 100%; box-sizing: border-box; }
        </style>
    </head>
    <body>
        <div class="grid-container" id="tile-grid"></div>
        <script>
            const tegels = $tegels_json;
            const alleDossiers = $alle_dossiers_json;

            function getImageUrl(fileId) { return "https://lh3.googleusercontent.com/d/" + fileId; }
            function getFallbackUrl(fileId) { return "https://drive.google.com/thumbnail?id=" + fileId + "&sz=w1600"; }

            function renderTiles() {
                const grid = document.getElementById('tile-grid');
                grid.innerHTML = '';
                tegels.forEach((item) => {
                    const tile = document.createElement('div');
                    tile.className = 'tile';
                    tile.onclick = () => openDriveOverlay(item.doc_id);
                    tile.innerHTML = `
                        <div class="img-container">
                            <img src="$${getImageUrl(item.id)}" onerror="this.onerror=null; this.src='$${getFallbackUrl(item.id)}';" loading="lazy" />
                        </div>
                        <div class="tile-caption">$${item.display_label || item.doc_id}</div>
                    `;
                    grid.appendChild(tile);
                });
            }

            function openDriveOverlay(docId) {
                const topDoc = window.top.document;
                const dossierPaginas = alleDossiers[docId] || [];
                let currentIndex = 0;

                let scale = 1;
                let pointX = 0;
                let pointY = 0;
                let isDragging = false;
                let startX = 0;
                let startY = 0;

                const modal = topDoc.createElement('div');
                modal.id = 'rbc-drive-modal';
                modal.style.cssText = `position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background-color: rgba(0,0,0,0.92); z-index: 9999999; display: flex; flex-direction: column; font-family: sans-serif; user-select: none;`;

                modal.innerHTML = `
                    <div style="height: 50px; background: #141414; display: flex; align-items: center; justify-content: space-between; padding: 0 20px; color: white; flex-shrink: 0; z-index: 10;">
                        <div style="display: flex; align-items: center;">
                            <button id="rbc-close-btn" style="background: transparent; border: none; color: white; font-size: 24px; cursor: pointer; padding: 5px 10px; margin-right: 15px;">✕</button>
                            <div id="rbc-title-info" style="font-size: 15px; font-weight: 500;">Laden...</div>
                        </div>
                        <div id="rbc-zoom-controls" style="display: flex; gap: 10px; align-items: center;">
                            <button id="rbc-reset-zoom" style="background: #333; border: 1px solid #555; color: white; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 12px;">Reset Zoom</button>
                        </div>
                    </div>
                    <div id="rbc-content-body" style="position: relative; flex: 1; overflow: hidden; display: flex; align-items: center; justify-content: center; cursor: grab;">
                    </div>
                `;

                topDoc.body.appendChild(modal);
                topDoc.body.style.overflow = 'hidden';

                function resetTransform() {
                    scale = 1;
                    pointX = 0;
                    pointY = 0;
                    applyTransform();
                }

                function applyTransform() {
                    const img = topDoc.getElementById('rbc-img');
                    if (img) {
                        img.style.transform = `translate($${pointX}px, $${pointY}px) scale($${scale})`;
                    }
                }

                function setupPanAndZoom(container, img) {
                    container.onwheel = function(e) {
                        e.preventDefault();
                        const oldScale = scale;
                        
                        const delta = -e.deltaY;
                        if (delta > 0) {
                            scale *= 1.15;
                        } else {
                            scale /= 1.15;
                        }

                        scale = Math.min(Math.max(0.8, scale), 8);

                        const factor = scale / oldScale;
                        pointX *= factor;
                        pointY *= factor;

                        applyTransform();
                    };

                    container.onmousedown = function(e) {
                        if (e.target.tagName === 'BUTTON' || e.target.id === 'rbc-prev-btn' || e.target.id === 'rbc-next-btn') return;
                        e.preventDefault();
                        isDragging = true;
                        startX = e.clientX - pointX;
                        startY = e.clientY - pointY;
                        container.style.cursor = 'grabbing';
                    };

                    topDoc.onmousemove = function(e) {
                        if (!isDragging) return;
                        e.preventDefault();
                        pointX = e.clientX - startX;
                        pointY = e.clientY - startY;
                        applyTransform();
                    };

                    topDoc.onmouseup = function() {
                        if (isDragging) {
                            isDragging = false;
                            container.style.cursor = 'grab';
                        }
                    };
                }

                function updateViewer() {
                    resetTransform();
                    const item = dossierPaginas[currentIndex];
                    const container = topDoc.getElementById('rbc-content-body');
                    const zoomControls = topDoc.getElementById('rbc-zoom-controls');
                    const isPdf = item.naam.toLowerCase().endsWith('.pdf') || (item.mime && item.mime.includes('pdf'));

                    topDoc.getElementById('rbc-title-info').innerText = `$${item.naam} ($${currentIndex + 1}/$${dossierPaginas.length})`;

                    if (isPdf) {
                        zoomControls.style.display = 'none';
                        container.innerHTML = `
                            <iframe src="https://drive.google.com/file/d/$${item.id}/preview" 
                                    style="width: 100%; height: 100%; border: none; background: #fff;">
                            </iframe>
                        `;
                    } else {
                        zoomControls.style.display = 'flex';
                        container.innerHTML = `
                            <div id="rbc-img-wrapper" style="width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; overflow: hidden;">
                                <img id="rbc-img" 
                                     style="max-width: 95vw; max-height: 90vh; object-fit: contain; transform-origin: center center; box-shadow: 0 4px 25px rgba(0,0,0,0.6);" 
                                     src="$${getImageUrl(item.id)}" 
                                     onerror="this.onerror=null; this.src='$${getFallbackUrl(item.id)}';" />
                            </div>
                            <div id="rbc-prev-btn" style="position: fixed; left: 20px; top: 50%; transform: translateY(-50%); font-size: 36px; color: white; cursor: pointer; user-select: none; background: rgba(0,0,0,0.5); padding: 8px 16px; border-radius: 50%; z-index: 20;">‹</div>
                            <div id="rbc-next-btn" style="position: fixed; right: 20px; top: 50%; transform: translateY(-50%); font-size: 36px; color: white; cursor: pointer; user-select: none; background: rgba(0,0,0,0.5); padding: 8px 16px; border-radius: 50%; z-index: 20;">›</div>
                        `;

                        const img = topDoc.getElementById('rbc-img');
                        setupPanAndZoom(container, img);

                        topDoc.getElementById('rbc-prev-btn').onclick = (e) => { e.stopPropagation(); if (currentIndex > 0) { currentIndex--; updateViewer(); } };
                        topDoc.getElementById('rbc-next-btn').onclick = (e) => { e.stopPropagation(); if (currentIndex < dossierPaginas.length - 1) { currentIndex++; updateViewer(); } };
                        topDoc.getElementById('rbc-reset-zoom').onclick = () => resetTransform();
                    }
                 updateViewer();
            }
            renderTiles();
        </script>
    </body>
    </html>
    """)

    grid_html = html_template.substitute(
        tegels_json=json.dumps(tegel_items),
        alle_dossiers_json=json.dumps(dossiers_dict)
    )

    aantal_tegels = len(tegel_items)
    aantal_rijen = math.ceil(aantal_tegels / 5) if aantal_tegels > 0 else 1
    components.html(grid_html, height=(aantal_rijen * 240) + 15, scrolling=False)

# ------------------------------------------------------------------------------
# 6. MULTIMODAL HISTORISCHE ANALYSE VIA GEMINI (OPTIE A: DIRECT DRIVE FILE READING)
# ------------------------------------------------------------------------------
if st.session_state.blader_paginas and not st.session_state.chat_historie:
    with st.spinner("Stap 3/3: Originele PDF's/Afbeeldingen ophalen & Historische analyse genereren..."):
        try:
            onderzoeks_prompt = f"""
Jij bent een zeer nauwkeurige historisch archivarisexpert voor een Belgisch archief.
Analyseer de meegeleverde originele bestanden (PDF's / afbeeldingen) EN de metadata-samenvattingen grondig en geef een gedetailleerd antwoord op de vraag.

STRIKTE INSTRUCTIES VOOR MODEL- EN BRONANALYSE:
1. **ELK MODEL APART:** Als er meerdere modellen of uitvoeringen voorkomen (bijv. 'Model Vedette' én 'Model Vedette 936'), maak dan voor ELK model een AFZONDERLIJK kopje.
2. **GEBRUIK EXACTE GEGEVENS UIT DE BESTANDEN:** Lees en bekijk de meegeleverde originele documenten zorgvuldig. Neem ALLE specifieke gegevens (zoals exacte jaartallen, buizenbezetting, afmetingen, golfbanden en serie-aanduidingen) over zoals ze LETTERLIJK in het document of op de afbeelding staan.
3. **GEEN FANTASIE OF GISSINGEN:** Neem geen jaartallen of buizentypes aan die niet in het originele document staan. Als een document vermeldt "Jaar: 1934-1935" of "Buizen: 2A7 58 2A6 2A5 80", neem deze feiten dan EXACT zo over in het rapport.

GEBRUIKERSVRAAG: {st.session_state.huidige_vraag}
"""
            payload = [onderzoeks_prompt]

            # 1. Metadata uit Google Sheet toevoegen
            sheet_data = getattr(st.session_state, 'sheet_dossier_data', [])
            tekst_gebundeld = "\n--- INHOUDSOPGAVE METADATA ---\n"
            for r in sheet_data:
                tekst_gebundeld += f"Bestand: {r.get('Bestandsnaam', '')} | Personen: {r.get('Genoemde Personen', '')} | Inhoud: {r.get('Inhoud & Cijfers (NL)', '')}\n"
            payload.append(tekst_gebundeld)

            # 2. De originele PDF/Afbeelding bestanden van de TOP-dossiers rechtstreeks downloaden en toevoegen
            top_dossier_ids = st.session_state.geselecteerde_doc_ids[:3]  # Pak de top 3 hoogst scorende dossiers
            toegevoegde_bestanden_count = 0

            for p in st.session_state.blader_paginas:
                if p.get("doc_id") in top_dossier_ids and toegevoegde_bestanden_count < 5:
                    file_id = p.get("id")
                    file_name = p.get("naam", "").lower()
                    mime_type = p.get("mime", "")

                    # Bepaal het MIME type als het niet bekend is
                    if not mime_type or mime_type == 'application/octet-stream':
                        if file_name.endswith('.pdf'):
                            mime_type = 'application/pdf'
                        elif file_name.endswith('.jpg') or file_name.endswith('.jpeg'):
                            mime_type = 'image/jpeg'
                        elif file_name.endswith('.png'):
                            mime_type = 'image/png'

                    # Download het bestand vanuit Google Drive als binary data
                    if file_id and mime_type in ['application/pdf', 'image/jpeg', 'image/png']:
                        try:
                            file_bytes = drive_service.files().get_media(fileId=file_id).execute()
                            
                            # Voeg het fysieke bestand direct toe aan de Gemini Payload
                            payload.append(types.Part.from_bytes(
                                data=file_bytes,
                                mime_type=mime_type
                            ))
                            toegevoegde_bestanden_count += 1
                        except Exception as e_dl:
                            st.caption(f"Kon {file_name} niet rechtstreeks downloaden: {e_dl}")

            st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
            analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, payload)
            st.session_state.chat_historie.append(("assistant", analyse_response.text))
            gc.collect()
            st.rerun()
        except Exception as e:
            st.error(f"Fout bij historische analyse: {e}")

if st.session_state.chat_historie:
    st.divider()
    st.subheader("📑 Historisch Onderzoeksrapport")
    for rol, tekst in st.session_state.chat_historie:
        with st.chat_message(rol):
            st.write(tekst)
