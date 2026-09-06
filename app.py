import io
import time
import string
import logging
import warnings
import json
import re
import gc
import math
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
APP_VERSION = "v1.2.5 (Slimme Scoring, Stopwoorden & API Stabiliteit)"
APP_DATE = "2026"

logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

def natuurlijke_sortering(item):
    tekst = item.get('naam', '') if isinstance(item, dict) else str(item)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', tekst)]

def normaliseer_tekst(tekst):
    """Zet tekst om naar lowercase en vangt spellingsvariaties op (zoals ij <-> y)."""
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
# 2. CONFIGURATIE & MODEL SELECTIE (STABIELER MODEL EERST)
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

# STOPWOORDEN LIJST - Deze woorden mogen NIET worden gebruikt voor ranking
NEDERLANDSE_STOPWOORDEN = {
    'wanneer', 'hoe', 'wat', 'wie', 'waar', 'is', 'van', 'de', 'het', 'een', 'en', 'in', 
    'op', 'te', 'dat', 'die', 'met', 'voor', 'zijn', 'was', 'er', 'ze', 'om', 'over', 
    'aan', 'bij', 'naar', 'uit', 'door', 'je', 'hij', 'we', 'ze', 'om', 'of', 'tot',
    'relevante', 'termen', 'gegenereerd', 'zoekopdracht', 'uitbreiden'
}

def bepaal_werkend_model(client):
    kandidaten = [
        'gemini-flash-latest',       # Stabieler model eerst proberen
        'gemini-flash-lite-latest'   # Snellere limieten als backup
    ]
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
            # Check voor 429 of RESOURCE_EXHAUSTED
            if "503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if poging < max_retries - 1:
                    wachttijd = 20 * (poging + 1)
                    st.info(f"⏳ Gemini API-limiet bereikt. Geforceerde pauze van {wachttijd} seconden...")
                    time.sleep(wachttijd)
                    continue
                else:
                    st.error("⚠️ De limiet voor de Gemini API is bereikt. Wacht 1-2 minuten voordat je opnieuw zoekt.")
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
if "uitgebreide_zoektermen" not in st.session_state:
    st.session_state.uitgebreide_zoektermen = []

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE & ZIJBALK
# ------------------------------------------------------------------------------
st.set_page_config(page_title="RBC Archief zoekmachine", page_icon="🔍", layout="wide")

with st.sidebar:
    st.title("ℹ️ Help & Info")
    with st.expander("🚨 API Limieten & Foutmeldingen"):
        st.markdown("""
        **1. Rood blok (429 RESOURCE_EXHAUSTED)?**
        Je hebt de limiet van de gratis Gemini API bereikt.
        
        **Nieuw in v1.2.5:** De code last nu automatisch pauzes in tussen de expansion en de analyse. Mocht je deze fout toch zien: wacht 1-2 minuten voordat je opnieuw op 'Voer onderzoek uit' klikt.
        """)

col_title, col_ver = st.columns([4, 1])
with col_title:
    st.title("🔍 RBC Archief zoekmachine")
with col_ver:
    st.caption(f"**Versie:** `{APP_VERSION}` ({APP_DATE})")

if MODEL_NAAM:
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")
else:
    st.error("Kon geen werkend Gemini-model vinden.")
    st.stop()

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: wanneer overleed emile delvoie?',
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
    st.session_state.uitgebreide_zoektermen = []
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
    st.warning("⚠️ Onderzoek is direct geannuleerd.")
    st.stop()

# ------------------------------------------------------------------------------
# 4. SLIMME SCORING & QUERY EXPANSION LOGICA (v1.2.5)
# ------------------------------------------------------------------------------
if st.session_state.start_zoekopdracht:
    if not st.session_state.huidige_vraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
        st.session_state.start_zoekopdracht = False
    else:
        # --- Stap 1a: Inhoudsopgave ophalen ---
        with st.spinner("Stap 1/3: Inhoudsopgave (Google Sheet) scannen..."):
            try:
                sh = gc_drive.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.session_state.start_zoekopdracht = False
                st.stop()

            if not data:
                st.error("De Google Sheet bevat geen gegevens.")
                st.session_state.start_zoekopdracht = False
                st.stop()

        # --- Stap 1b: Query Expansion via Gemini ---
        with st.spinner("Stap 1b/3: Zoekopdracht uitbreiden (Query Expansion)..."):
            vraag_orig = st.session_state.huidige_vraag
            prompt_expansion = f"""
Jij bent een zoekmachine-expert voor een historisch archief. 
Analyseer de onderstaande gebruikersvraag en genereer een lijst van 5 tot 12 synoniemen, gerelateerde termen, spellingsvarianten of trefwoorden.
Gebruik GEEN generieke stopwoorden zoals "wanneer", "hoe", "wat", "is", "van", "de".

GEBRUIKERSVRAAG: "{vraag_orig}"

Geef ALLEEN een JSON-array van strings terug, bijvoorbeeld:
["term1", "term2", "term3"]
"""
            uitgebreide_termen = []
            try:
                expansion_res = genereer_met_retry(ai_client, MODEL_NAAM, prompt_expansion)
                json_match = re.search(r'\[.*\]', expansion_res.text, re.DOTALL)
                if json_match:
                    uitgebreide_termen = json.loads(json_match.group(0))
            except Exception:
                uitgebreide_termen = []

            # Haal originele woorden op uit vraag
            vraag_norm = normaliseer_tekst(vraag_orig)
            basis_woorden = [w for w in re.sub(r'[^\w\s]', ' ', vraag_norm).split() if len(w) >= 3]
            
            # Combineer en filter unieke zoektermen
            alle_zoektermen_unfiltered = list(set([normaliseer_tekst(t) for t in uitgebreide_termen + basis_woorden if t]))
            
            # HARD FILTER: Verwijder stopwoorden uit de zoeklijst
            alle_zoektermen = [t for t in alle_zoektermen_unfiltered if t not in NEDERLANDSE_STOPWOORDEN]
            st.session_state.uitgebreide_zoektermen = alle_zoektermen
            
            # --- API VEILIGHEID: GEFORCEERDE PAUZE ---
            # Gratis API heeft limieten per minuut. We pauzeren hier om te voorkomen dat Stap 3 direct botst.
            time.sleep(12) 

        # --- Stap 1c: Geoptimaliseerde Scoring voor Personen & Familie Matching ---
        with st.spinner("Stap 2/3: Archiefstukken & PDF's matchen en rangschikken..."):
            dossier_scores = {}

            # Bepaal de belangrijkste namen uit de vraag
            # We filteren de stopwoorden hier ook uit de basiswoorden voor de score
            kern_namen = [w for w in basis_woorden if w not in NEDERLANDSE_STOPWOORDEN]

            for row in data:
                doc_id = str(row.get('Document_ID', '')).strip()
                b_naam = str(row.get('Bestandsnaam', '')).strip()
                datum_veld = str(row.get('Datum', '')).strip()
                if not doc_id:
                    doc_id = f"SINGLE_{b_naam}"

                pers = normaliseer_tekst(row.get('Genoemde Personen') or row.get('Genoemde personen') or '')
                ond = normaliseer_tekst(row.get('Onderwerp (NL)') or row.get('Onderwerp') or '')
                inhoud = normaliseer_tekst(row.get('Inhoud & Cijfers (NL)') or row.get('Inhoud & cijfers') or row.get('Inhoud') or '')
                
                combi_tekst = f"{doc_id.lower()} {b_naam.lower()} {datum_veld.lower()} {pers} {ond} {inhoud}"

                score = 0

                # 1. HOGE PRIORITEIT: Match op kern-namen (bijv. Emile, Delvoie)
                for kn in kern_namen:
                    if kn in pers:
                        score += 150 # Persoonsnaam match is zeer sterk
                    elif kn in combi_tekst:
                        score += 50 # Naam ergens anders in tekst

                # 2. PDF BONUS: PDF bestanden waarin de naam voorkomt, krijgen voorrang
                # Omdat PDF's vaak samenvattende dossiers zijn
                if any(kn in combi_tekst for kn in kern_namen) and b_naam.lower().endswith('.pdf'):
                    score += 200

                # 3. Punten voor matches vanuit Query Expansion (familie, overlyden, etc.)
                # Telt minder zwaar dan de naam
                for term in st.session_state.uitgebreide_zoektermen:
                    if term in combi_tekst:
                        score += 20

                if score > 0:
                    dossier_scores[doc_id] = dossier_scores.get(doc_id, 0) + score

            gesorteerde_dossiers = [d_id for d_id, sc in sorted(dossier_scores.items(), key=lambda x: x[1], reverse=True)]

            if not gesorteerde_dossiers:
                gesorteerde_dossiers = list(set(str(row.get('Document_ID', '')).strip() for row in data if row.get('Document_ID')))

            st.session_state.geselecteerde_doc_ids = gesorteerde_dossiers[:max_dossiers]

        if not st.session_state.geselecteerde_doc_ids:
            st.warning("⚠️ Geen relevante documenten gevonden voor deze vraag.")
            st.session_state.start_zoekopdracht = False
            st.stop()

        sheet_dossier_data = []
        gezochte_bestanden = []
        
        for row in data:
            doc_id = str(row.get('Document_ID', '')).strip()
            b_naam = str(row.get('Bestandsnaam', '')).strip()
            if not doc_id:
                doc_id = f"SINGLE_{b_naam}"

            if any(doc_id.lower() == g_id.lower() or b_naam.lower() == g_id.lower() for g_id in st.session_state.geselecteerde_doc_ids):
                sheet_dossier_data.append(row)
                if b_naam:
                    # Fuzzy matching voor extensies (Drive name query is erg strikt)
                    schoon_naam = b_naam.split('/')[-1]
                    gezochte_bestanden.append((doc_id, schoon_naam))
                    # Voeg extensie varianten toe voor matching in drive
                    if not schoon_naam.lower().endswith('.pdf'):
                        gezochte_bestanden.append((doc_id, f"{schoon_naam}.pdf"))

        blader_lijst = []
        if gezochte_bestanden:
            batch_size = 50
            drive_map = {}
            unieke_zoeknamen = list(set([naam for _, naam in gezochte_bestanden]))

            for i in range(0, len(unieke_zoeknamen), batch_size):
                batch = unieke_zoeknamen[i:i + batch_size]
                namen_query = " or ".join([f"name = '{naam}'" for naam in batch])
                query = f"({namen_query}) and trashed = false"
                
                res = drive_service.files().list(
                    q=query, 
                    fields='files(id, name, mimeType)',
                    pageSize=1000
                ).execute().get('files', [])
                
                for f in res:
                    drive_map[f['name'].lower()] = f
            
            for doc_id, b_schoon in gezochte_bestanden:
                b_key = b_schoon.lower()
                if b_key in drive_map:
                    f = drive_map[b_key]
                    # Voorkom dubbele bestanden in bladerlijst (bij fuzzy matching)
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
# 5. WEERGAVE VAN DE FOTOTEGELS & NATIVE OVERLAY VIEWER
# ------------------------------------------------------------------------------
if st.session_state.blader_paginas:
    st.divider()
    
    if st.session_state.uitgebreide_zoektermen:
        st.info(f"💡 **Query Expansion toegepast:** Relevante termen gegenereerd -> `{', '.join(st.session_state.uitgebreide_zoektermen[:8])}`")

    dossiers_dict = {}
    for p in st.session_state.blader_paginas:
        d_id = p.get("doc_id", "Dossier_Onbekend")
        if d_id not in dossiers_dict:
            dossiers_dict[d_id] = []
        dossiers_dict[d_id].append(p)

    for d_id in dossiers_dict:
        dossiers_dict[d_id].sort(key=natuurlijke_sortering)

    tegel_items = []
    volgorde_ids = st.session_state.geselecteerde_doc_ids if st.session_state.geselecteerde_doc_ids else list(dossiers_dict.keys())

    for d_id in volgorde_ids:
        if d_id in dossiers_dict and dossiers_dict[d_id]:
            pagina_lijst = dossiers_dict[d_id]
            eerste_pagina = pagina_lijst[0].copy()
            
            aantal_pags = len(pagina_lijst)
            if aantal_pags > 1:
                eerste_pagina["display_label"] = f"{d_id} ({aantal_pags} bestanden/pag.)"
            else:
                eerste_pagina["display_label"] = d_id
                
            tegel_items.append(eerste_pagina)

    st.subheader(f"🖼️ Geselecteerde Archiefdocumenten ({len(tegel_items)} dossiers • {len(st.session_state.blader_paginas)} bestanden/pagina's)")
    st.caption("Klik op een tegel om het document/boek of de PDF te openen in de viewer.")

    tegels_json = json.dumps(tegel_items)
    alle_dossiers_json = json.dumps(dossiers_dict)

    grid_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{ margin: 0; padding: 5px 0; font-family: -apple-system, sans-serif; background: transparent; }}
            .grid-container {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 15px; width: 100%; }}
            .tile {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; cursor: pointer; transition: transform 0.2s; display: flex; flex-direction: column; align-items: center; box-shadow: 0 2px 5px rgba(0,0,0,0.05); overflow: hidden; }}
            .tile:hover {{ transform: translateY(-3px); box-shadow: 0 6px 15px rgba(0,0,0,0.15); border-color: #1a73e8; }}
            .img-container {{ width: 100%; height: 180px; background-color: #f5f5f5; display: flex; align-items: center; justify-content: center; overflow: hidden; }}
            .img-container img {{ width: 100%; height: 100%; object-fit: cover; }}
            .tile-caption {{ padding: 10px 8px; font-size: 12px; font-weight: 600; color: #202124; text-align: center; line-height: 1.3; background: #fafafa; border-top: 1px solid #f0f0f0; width: 100%; box-sizing: border-box; }}
        </style>
    </head>
    <body>
        <div class="grid-container" id="tile-grid"></div>
        <script>
            const tegels = {tegels_json};
            const alleDossiers = {alle_dossiers_json};

            function getImageUrl(fileId) {{ return "https://lh3.googleusercontent.com/d/" + fileId; }}
            function getFallbackUrl(fileId) {{ return "https://drive.google.com/thumbnail?id=" + fileId + "&sz=w600"; }}

            function renderTiles() {{
                const grid = document.getElementById('tile-grid');
                tegels.forEach((item) => {{
                    const tile = document.createElement('div');
                    tile.className = 'tile';
                    tile.onclick = () => openDriveOverlay(item.doc_id);
                    const labelTekst = item.display_label || item.doc_id;
                    tile.innerHTML = `
                        <div class="img-container">
                            <img src="${{getImageUrl(item.id)}}" onerror="this.onerror=null; this.src='${{getFallbackUrl(item.id)}}';" loading="lazy" />
                        </div>
                        <div class="tile-caption">${{labelTekst}}</div>
                    `;
                    grid.appendChild(tile);
                }});
            }}

            function openDriveOverlay(docId) {{
                const topDoc = window.top.document;
                const dossierPaginas = alleDossiers[docId] || [];
                let currentIndex = 0;

                const modal = topDoc.createElement('div');
                modal.id = 'rbc-drive-modal';
                modal.style.cssText = `position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; background-color: rgba(0,0,0,0.92); z-index: 9999999; display: flex; flex-direction: column; font-family: sans-serif; `;

                modal.innerHTML = `
                    <div style="height: 56px; background: #141414; display: flex; align-items: center; padding: 0 20px; color: white; border-bottom: 1px solid rgba(255,255,255,0.1);">
                        <button id="rbc-close-btn" style="background: transparent; border: none; color: white; font-size: 24px; cursor: pointer;" title="Sluiten (ESC)">✕</button>
                        <div id="rbc-title-info" style="font-size: 15px; margin-left: 15px;">Laden...</div>
                    </div>
                    <div style="position: relative; flex: 1; display: flex; align-items: center; justify-content: center; overflow: hidden;">
                        <img id="rbc-img" style="max-width: 90%; max-height: 90%; object-fit: contain; border-radius: 4px; box-shadow: 0 0 25px rgba(0,0,0,0.8); transition: opacity 0.2s;" src="" />
                        <div id="rbc-prev-btn" style="position: absolute; top: 50%; left: 20px; font-size: 40px; color: white; cursor: pointer; user-select: none;">‹</div>
                        <div id="rbc-next-btn" style="position: absolute; top: 50%; right: 20px; font-size: 40px; color: white; cursor: pointer; user-select: none;">›</div>
                    </div>
                `;

                topDoc.body.appendChild(modal);
                topDoc.body.style.overflow = 'hidden';

                function updateViewer() {{
                    const item = dossierPaginas[currentIndex];
                    const imgEl = topDoc.getElementById('rbc-img');
                    imgEl.style.opacity = '0.3';
                    imgEl.src = getImageUrl(item.id);
                    imgEl.onload = () => {{ imgEl.style.opacity = '1'; }};
                    topDoc.getElementById('rbc-title-info').innerText = `${{item.naam}}  •  Bestand ${{currentIndex + 1}} van ${{dossierPaginas.length}}`;
                }}

                function sluitModal() {{
                    modal.remove();
                    topDoc.body.style.overflow = 'auto';
                    topDoc.removeEventListener('keydown', keyHandler);
                }}

                function keyHandler(e) {{
                    if (e.key === 'Escape') sluitModal();
                    if (e.key === 'ArrowLeft' && currentIndex > 0) {{ currentIndex--; updateViewer(); }}
                    if (e.key === 'ArrowRight' && currentIndex < dossierPaginas.length - 1) {{ currentIndex++; updateViewer(); }}
                }}

                topDoc.getElementById('rbc-close-btn').onclick = sluitModal;
                topDoc.getElementById('rbc-prev-btn').onclick = () => {{ if (currentIndex > 0) {{ currentIndex--; updateViewer(); }} }};
                topDoc.getElementById('rbc-next-btn').onclick = () => {{ if (currentIndex < dossierPaginas.length - 1) {{ currentIndex++; updateViewer(); }} }};
                topDoc.addEventListener('keydown', keyHandler);

                updateViewer();
            }}
            renderTiles();
        </script>
    </body>
    </html>
    """

    aantal_tegels = len(tegel_items)
    aantal_rijen = math.ceil(aantal_tegels / 5) if aantal_tegels > 0 else 1
    berekende_hoogte = (aantal_rijen * 240) + 15

    components.html(grid_html, height=berekende_hoogte, scrolling=False)

# ------------------------------------------------------------------------------
# 6. GECOMBINEERDE VISUELE & METADATA ANALYSE VIA GEMINI (v1.2.5 STABIELER)
# ------------------------------------------------------------------------------
if st.session_state.blader_paginas and not st.session_state.chat_historie:
    with st.spinner("Stap 3/3: Historische en visuele analyse uitvoeren via Gemini..."):
        try:
            onderzoeks_prompt = f"""
Jij bent een financieel-historisch expert en archivaris.
Beantwoord de onderstaande onderzoeksvraag grondig en gedetailleerd op basis van de meegeleverde originele archiefstukken (zowel tekst als afbeeldingen/PDF's).

ONDERZOEKSVRAAG: {st.session_state.huidige_vraag}

INSTRUCTIES VOOR JE RAPPORT:
1. Richt je specifiek op de gevraagde thema's, personen, locaties, bedragen, boeken, radiomodellen of documenten.
2. Vermeld expliciet ÁLLE betrokken namen van personen (bijv. eisers, gemaaktigden, bestuurders, oprichters) én de officiële bedrijfsnamen.
3. Structureer je antwoord helder met duidelijke kopjes en een conclusie.
4. Citeer steeds de bestandsnaam (bijv. 'DOC_0004', 'Delvoie_familie.pdf') wanneer je naar specifieke informatie verwijst.
"""
            payload = [onderzoeks_prompt]
            sheet_data = getattr(st.session_state, 'sheet_dossier_data', [])

            # 1. Voeg Sheet metadata toe
            tekst_gebundeld = f"\n--- REGISTERGEGEVENS VAN SELECTIE ({len(sheet_data)} RECORD(S)) ---\n"
            for idx, r in enumerate(sheet_data, start=1):
                doc_id = r.get('Document_ID', '')
                b_naam = r.get('Bestandsnaam', '')
                pers = r.get('Genoemde Personen', '') or r.get('Genoemde personen', '')
                ond = r.get('Onderwerp (NL)', '') or r.get('Onderwerp', '')
                inhoud = r.get('Inhoud & Cijfers (NL)') or r.get('Inhoud & cijfers') or r.get('Inhoud', '')

                tekst_gebundeld += f"\n[Item {idx}] Doc_ID: {doc_id} | Bestand: {b_naam}\n"
                if pers: tekst_gebundeld += f"  - Personen: {pers}\n"
                if ond: tekst_gebundeld += f"  - Onderwerp: {ond}\n"
                if inhoud: tekst_gebundeld += f"  - Inhoud: {inhoud}\n"

            payload.append(tekst_gebundeld)

            # 2. Voeg afbeeldingen en PDF's toe
            max_fotos = 15
            geüploade_fotos = 0

            # We gebruiken hier het geselecteerde Drive-id om het bestand op te halen
            for item in st.session_state.blader_paginas:
                if geüploade_fotos >= max_fotos:
                    break
                try:
                    req = drive_service.files().get_media(fileId=item['id'])
                    f_data = req.execute()
                    
                    if item.get('mime') == 'application/pdf' or item['naam'].lower().endswith('.pdf'):
                        pdf_part = types.Part.from_bytes(data=f_data, mime_type='application/pdf')
                        payload.append(f"\n--- ORIGINELE PDF: {item['naam']} ---")
                        payload.append(pdf_part)
                        geüploade_fotos += 1
                    else:
                        img = Image.open(io.BytesIO(f_data))
                        if img.mode != 'RGB':
                            img = img.convert('RGB')
                        
                        img.thumbnail((600, 600))
                        img_byte_arr = io.BytesIO()
                        img.save(img_byte_arr, format='JPEG', quality=65)

                        img_part = types.Part.from_bytes(data=img_byte_arr.getvalue(), mime_type='image/jpeg')
                        payload.append(f"\n--- ORIGINELE AFBEELDING: {item['naam']} ---")
                        payload.append(img_part)
                        geüploade_fotos += 1
                        
                        del img; del f_data; del img_byte_arr
                except Exception:
                    continue

            # API VEILIGHEID: GEFORCEERDE PAUZE VOOR DE ANALYSE
            # time.sleep(5) 

            st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
            analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, payload)
            st.session_state.chat_historie.append(("assistant", analyse_response.text))
            gc.collect()
            st.rerun()
        except Exception as e:
            st.error(f"Er is een fout opgetreden bij de verwerking door Gemini: {e}")

if st.session_state.chat_historie:
    st.divider()
    st.subheader("📑 Historisch Onderzoeksrapport")
    
    for rol, tekst in st.session_state.chat_historie:
        with st.chat_message(rol):
            st.write(tekst)

    if vervolgvraag := st.chat_input("Stel een vervolgvraag over dit rapport of boek..."):
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
                    st.error(f"Fout bij beantwoorden van vervolgvraag: {e}")
