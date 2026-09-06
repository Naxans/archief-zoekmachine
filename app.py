import io
import time
import logging
import warnings
import base64
import streamlit as st
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google import genai
from google.genai import types

# ==============================================================================
# ARCHIEF ZOEKMACHINE - VERSIE INFORMATIE
# ==============================================================================
# Versie: v1.2.1
# Datum: September 2026
#
# FEATURE / FIX:
# - Herstel van de originele, strakke, donkere fullscreen overlay (Foto 1).
# - Base64 afbeeldingsweergave om 30s timeouts/crashes definitief te voorkomen.
# ==============================================================================

APP_VERSIE = "v1.2.1 (2026)"

logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

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
    gc = gspread.authorize(creds)
    drive_service = build('drive', 'v3', credentials=creds)
    ai_client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
    return gc, drive_service, ai_client

try:
    gc, drive_service, ai_client = init_services()
except Exception as e:
    st.error(f"Fout bij verbinden met Google/Gemini diensten: {e}")
    st.stop()

# ------------------------------------------------------------------------------
# 2. CONFIGURATIE & HELPER FUNCTIES
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

def bepaal_werkend_model(client):
    kandidaten = ['gemini-flash-lite-latest', 'gemini-flash-latest']
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
                    time.sleep(15 * (poging + 1))
                    continue
            raise e

def laad_drive_bestand_payload(drive_service, file_id, mime_type, file_name):
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    
    file_bytes = fh.getvalue()

    if "pdf" in mime_type.lower() or file_name.lower().endswith(".pdf"):
        return types.Part.from_bytes(data=file_bytes, mime_type='application/pdf')
    else:
        img = Image.open(io.BytesIO(file_bytes))
        if img.mode != 'RGB': 
            img = img.convert('RGB')
        img.thumbnail((1200, 1200))
        img_byte_arr = io.BytesIO()
        img.save(img_byte_arr, format='JPEG', quality=85)
        return types.Part.from_bytes(data=img_byte_arr.getvalue(), mime_type='image/jpeg')

@st.cache_data(ttl=3600)
def haal_afbeelding_base64(file_id):
    """Haalt afbeelding op en converteert naar base64 voor snelle weergave in overlay zonder timeout"""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return base64.b64encode(fh.getvalue()).decode('utf-8')
    except Exception:
        return None

def verrijk_zoekopdracht_met_gemini(client, model, originele_vraag):
    prompt = f"""
Jij bent een taalkundig en historisch expert gespecialiseerd in Belgische bedrijfs- en archiefstukken.
De gebruiker stelt de volgende zoekvraag: "{originele_vraag}"

Geef een door komma's gescheiden lijst van synoniemen, actiewoorden en naamvarianten (bijv: Emile, Emiel, Delvoie, overleden, décès, geschiedenis).
"""
    try:
        res = genereer_met_retry(client, model, prompt)
        return res.text.strip()
    except Exception:
        return originele_vraag

# Session state initialisatie
if "actieve_chat" not in st.session_state:
    st.session_state.actieve_chat = None
if "chat_historie" not in st.session_state:
    st.session_state.chat_historie = []
if "bron_details" not in st.session_state:
    st.session_state.bron_details = []
if "totaal_paginas" not in st.session_state:
    st.session_state.totaal_paginas = 0
if "verrijkte_termen" not in st.session_state:
    st.session_state.verrijkte_termen = ""
if "laatste_vraag" not in st.session_state:
    st.session_state.laatste_vraag = ""

if "lightbox_dossier" not in st.session_state:
    st.session_state.lightbox_dossier = None
if "lightbox_pagina_idx" not in st.session_state:
    st.session_state.lightbox_pagina_idx = 0

# ------------------------------------------------------------------------------
# 3. INTERFACE & STYLING (DONKERE OVERLAY ZOALS FOTO 1)
# ------------------------------------------------------------------------------
st.set_page_config(page_title="RBC Archief zoekmachine", page_icon="🔍", layout="wide")

st.markdown("""
<style>
    .doc-card {
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 5px;
        text-align: center;
        background-color: #fcfcfc;
        margin-bottom: 5px;
    }
    .doc-card img {
        border-radius: 4px;
        max-height: 160px;
        object-fit: cover;
        width: 100%;
    }
    .doc-title {
        font-size: 11px;
        font-weight: 600;
        color: #444;
        margin-top: 6px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    /* FULLSCREEN OVERLAY CSS VOOR FOTO 1 WEERGAVE */
    .v121-overlay-backdrop {
        position: fixed;
        top: 0;
        left: 0;
        width: 100vw;
        height: 100vh;
        background-color: #121212;
        z-index: 999990;
    }
</style>
""", unsafe_allow_html=True)

# ------------------------------------------------------------------------------
# HOOFDPAGINA - BASIS WEERGAVE
# ------------------------------------------------------------------------------
header_col1, header_col2 = st.columns([4, 1])
with header_col1:
    st.title("🔍 RBC Archief zoekmachine")
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")
with header_col2:
    st.markdown(f"<p style='text-align: right; color: #888; font-size: 13px; margin-top: 15px;'>Versie: {APP_VERSIE}</p>", unsafe_allow_html=True)

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='wanneer is emile delvoie overleden?',
        height=90
    )
with col2:
    max_dossiers = st.slider("Max dossiers (Document_ID's):", min_value=5, max_value=50, value=20, step=5)

btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

if stop_button:
    st.warning("⚠️ Onderzoek geannuleerd.")
    st.stop()

# Placeholders
expansion_placeholder = st.empty()
docs_placeholder = st.empty()
rapport_placeholder = st.empty()

# Helper-functie om documentenraster op te bouwen
def toon_documenten_grid(container, bronnen, totaal_pags):
    with container.container():
        st.markdown("---")
        aantal_dossiers = len(bronnen)
        st.subheader(f"🖼️ Geselecteerde Archiefdocumenten ({aantal_dossiers} dossiers • {totaal_pags} pagina's)")
        
        cols = st.columns(6)
        for idx, bron in enumerate(bronnen):
            b_naam = bron["naam"]
            b_id = bron["eerst_id"]
            thumbnail_url = f"https://drive.google.com/thumbnail?id={b_id}&sz=w400"

            col = cols[idx % 6]
            with col:
                st.markdown(f"""
                    <div class="doc-card">
                        <img src="{thumbnail_url}" alt="{b_naam}">
                        <div class="doc-title">{b_naam}</div>
                    </div>
                """, unsafe_allow_html=True)
                
                if st.button("👁️ Bekijk", key=f"btn_view_{idx}", use_container_width=True):
                    st.session_state.lightbox_dossier = bron
                    st.session_state.lightbox_pagina_idx = 0
                    st.rerun()

# Helper-functie om de query expansion op te bouwen
def toon_expansion(container, vraag, termen):
    with container.container():
        with st.expander("🧠 Bekijk de door Gemini verrijkte zoektermen (Query Expansion)", expanded=True):
            st.write(f"**Originele vraag:** {vraag}")
            st.write(f"**Verrijkte trefwoorden & varianten:** {termen}")

# ------------------------------------------------------------------------------
# 4. ONDERZOEKSLOGICA
# ------------------------------------------------------------------------------
if submit_button:
    if not onderzoeksvraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
    else:
        st.session_state.chat_historie = []
        st.session_state.bron_details = []
        st.session_state.totaal_paginas = 0
        st.session_state.laatste_vraag = onderzoeksvraag

        # STAP 1: Query Expansion
        with st.spinner("🧠 Tussenstation: Trefwoorden verzamelen..."):
            st.session_state.verrijkte_termen = verrijk_zoekopdracht_met_gemini(ai_client, MODEL_NAAM, onderzoeksvraag)
            
        toon_expansion(expansion_placeholder, st.session_state.laatste_vraag, st.session_state.verrijkte_termen)

        # STAP 2: Inhoudsopgave scannen
        with st.spinner("Inhoudsopgave scannen..."):
            try:
                sh = gc.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Fout bij openen Google Sheet: {e}")
                st.stop()

            dossier_pagina_map = {}
            for row in data:
                doc_id = str(row.get('Document_ID', '')).strip()
                b_naam = str(row.get('Bestandsnaam', '')).strip()
                key = doc_id if doc_id else b_naam

                if key not in dossier_pagina_map:
                    dossier_pagina_map[key] = {"bestanden": [], "aantal_paginas": 0}

                if b_naam and b_naam not in dossier_pagina_map[key]["bestanden"]:
                    dossier_pagina_map[key]["bestanden"].append(b_naam)
                    dossier_pagina_map[key]["aantal_paginas"] += 1

            vraag_woorden = [w.strip().lower() for w in onderzoeksvraag.split() if len(w.strip()) > 3]
            hoofd_namen = [w for w in vraag_woorden if w not in ['wanneer', 'overleden', 'wie', 'wat', 'welke', 'waar']]

            prio_pdf_ids = []
            prio_doc_ids = []
            overige_doc_ids = []

            for row in data:
                doc_id_val = str(row.get('Document_ID', '')).strip()
                b_naam_val = str(row.get('Bestandsnaam', '')).strip()
                target_id = doc_id_val if doc_id_val else b_naam_val

                rij_tekst = f"{doc_id_val} {b_naam_val} {row.get('Genoemde Personen', '')} {row.get('Onderwerp (NL)', '')} {row.get('Inhoud & Cijfers (NL)', '')}".lower()

                if hoofd_namen and not any(naam in rij_tekst for naam in hoofd_namen):
                    continue

                if target_id not in prio_pdf_ids and target_id not in prio_doc_ids and target_id not in overige_doc_ids:
                    if "pdf" in b_naam_val.lower() or "delvoie.pdf" in target_id.lower():
                        prio_pdf_ids.append(target_id)
                    elif "geschiedenis" in rij_tekst or "overzicht" in rij_tekst:
                        prio_doc_ids.append(target_id)
                    else:
                        overige_doc_ids.append(target_id)

            geselecteerde_doc_ids = (prio_pdf_ids + prio_doc_ids + overige_doc_ids)[:max_dossiers]

        if not geselecteerde_doc_ids:
            st.warning("⚠️ Geen relevante documenten gevonden voor deze naam.")
            st.stop()

        # STAP 3: Payload ophalen
        onderzoeks_payload = [
            f"ONDERZOEKSVRAAG: {onderzoeksvraag}\nVERRIJKTE CONTEXT: {st.session_state.verrijkte_termen}\nBeantwoord de vraag zo nauwkeurig mogelijk. Controleer alle verstrekte PDF's en afbeeldingen op data en familienamen."
        ]

        with st.spinner("Documenten en PDF's ophalen uit Drive..."):
            for doc_id in geselecteerde_doc_ids:
                info = dossier_pagina_map.get(doc_id, {"bestanden": [doc_id], "aantal_paginas": 1})
                pag_count = info["aantal_paginas"]
                st.session_state.totaal_paginas += pag_count

                dossier_bestanden_lijst = []

                for idx, b_naam in enumerate(info["bestanden"]):
                    b_naam_schoon = str(b_naam).strip("'\" ")
                    if ":" in b_naam_schoon: b_naam_schoon = b_naam_schoon.split(":", 1)[-1].strip()
                    basis_naam = b_naam_schoon.split('/')[-1]

                    query = f"name contains '{basis_naam.rsplit('.', 1)[0]}' and trashed = false"
                    res = drive_service.files().list(q=query, fields='files(id, name, mimeType)').execute()
                    bestanden = res.get('files', [])

                    if bestanden:
                        f = bestanden[0]
                        b_id, b_real_naam, mime_type = f['id'], f['name'], f.get('mimeType', '')
                        dossier_bestanden_lijst.append({"id": b_id, "bestandsnaam": b_real_naam})

                        try:
                            payload_part = laad_drive_bestand_payload(drive_service, b_id, mime_type, b_real_naam)
                            onderzoeks_payload.append(f"\n--- DOSSIER/DOCUMENT: {doc_id} (Pagina {idx+1}/{pag_count}) ---")
                            onderzoeks_payload.append(payload_part)
                        except Exception as ex:
                            st.write(f"Fout bij inladen {b_real_naam}: {ex}")

                if dossier_bestanden_lijst:
                    weergave_titel = f"{doc_id} ({pag_count} pag.)" if pag_count > 1 else doc_id
                    st.session_state.bron_details.append({
                        "naam": weergave_titel,
                        "eerst_id": dossier_bestanden_lijst[0]["id"],
                        "bestanden": dossier_bestanden_lijst
                    })

        # LIVE UPDATE STAP 2
        toon_documenten_grid(docs_placeholder, st.session_state.bron_details, st.session_state.totaal_paginas)

        # STAP 4: Gemini-analyse uitvoeren
        with st.spinner("📑 Historisch Onderzoeksrapport genereren met Gemini..."):
            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
            except Exception as e:
                st.error(f"Fout tijdens analyse: {e}")

# ------------------------------------------------------------------------------
# 5. PASSIEVE RENDERING
# ------------------------------------------------------------------------------
if st.session_state.verrijkte_termen and submit_button is False:
    toon_expansion(expansion_placeholder, st.session_state.laatste_vraag, st.session_state.verrijkte_termen)

if st.session_state.bron_details and submit_button is False:
    toon_documenten_grid(docs_placeholder, st.session_state.bron_details, st.session_state.totaal_paginas)

if st.session_state.chat_historie:
    with rapport_placeholder.container():
        st.markdown("---")
        st.subheader("📑 Historisch Onderzoeksrapport")

        for rol, tekst in st.session_state.chat_historie:
            with st.chat_message(rol):
                st.write(tekst)

        if vervolgvraag := st.chat_input("Stel een vervolgvraag over dit rapport..."):
            st.session_state.chat_historie.append(("user", vervolgvraag))
            with st.chat_message("user"):
                st.write(vervolgvraag)

            with st.chat_message("assistant"):
                with st.spinner("Analyseren..."):
                    try:
                        response = st.session_state.actieve_chat.send_message(vervolgvraag)
                        st.write(response.text)
                        st.session_state.chat_historie.append(("assistant", response.text))
                    except Exception as e:
                        st.error(f"Fout bij verwerken vervolgvraag: {e}")

# ------------------------------------------------------------------------------
# LIGHTBOX OVERLAY RENDERING (EXACT ZOALS FOTO 1)
# ------------------------------------------------------------------------------
if st.session_state.lightbox_dossier:
    dossier = st.session_state.lightbox_dossier
    bestanden = dossier["bestanden"]
    totaal_pags = len(bestanden)
    curr_idx = st.session_state.lightbox_pagina_idx
    actief_bestand = bestanden[curr_idx]

    # Haal base64 data op voor directe weergave zonder timeout
    img_b64 = haal_afbeelding_base64(actief_bestand["id"])
    img_src = f"data:image/jpeg;base64,{img_b64}" if img_b64 else f"https://drive.google.com/thumbnail?id={actief_bestand['id']}&sz=w1600"

    # Donkere Achtergrond
    st.markdown('<div class="v121-overlay-backdrop"></div>', unsafe_allow_html=True)

    # Top balk (Zwart met titel en sluitknop)
    top_col1, top_col2, top_col3 = st.columns([6, 2, 1])
    with top_col1:
        st.markdown(f"<span style='color: #fff; font-weight: bold;'>📄 {actief_bestand['bestandsnaam']}</span>", unsafe_allow_html=True)
    with top_col2:
        st.markdown(f"<span style='color: #aaa;'>Pagina {curr_idx + 1} van {totaal_pags}</span>", unsafe_allow_html=True)
    with top_col3:
        if st.button("✕ Sluiten", key="v121_close_btn", type="primary", use_container_width=True):
            st.session_state.lightbox_dossier = None
            st.rerun()

    # Middengebied: Navigatiepijlen + Afbeelding
    nav_col1, img_col, nav_col2 = st.columns([1, 10, 1])

    with nav_col1:
        st.markdown("<div style='height: 35vh;'></div>", unsafe_allow_html=True)
        if st.button("◀", key="v121_prev_btn", use_container_width=True, disabled=(curr_idx == 0)):
            st.session_state.lightbox_pagina_idx -= 1
            st.rerun()

    with img_col:
        st.markdown(f"""
            <div style="text-align: center; margin-top: 10px;">
                <img src="{img_src}" style="max-height: 75vh; max-width: 100%; border-radius: 4px; box-shadow: 0 0 20px rgba(0,0,0,0.8);">
            </div>
        """, unsafe_allow_html=True)

    with nav_col2:
        st.markdown("<div style='height: 35vh;'></div>", unsafe_allow_html=True)
        if st.button("▶", key="v121_next_btn", use_container_width=True, disabled=(curr_idx == totaal_pags - 1)):
            st.session_state.lightbox_pagina_idx += 1
            st.rerun()
