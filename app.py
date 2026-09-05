import io
import time
import string
import logging
import warnings
import json
import re
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
APP_VERSION = "v3.3.1"
APP_DATE = "2026"

logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

def natuurlijke_sortering(item):
    tekst = item.get('naam', '') if isinstance(item, dict) else str(item)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', tekst)]

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
# 2. CONFIGURATIE & DYNAMISCHE MODEL-DETECTIE
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

def bepaal_werkend_model(client):
    kandidaten = [
        'gemini-flash-lite-latest',
        'gemini-flash-latest'
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
            if "503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if poging < max_retries - 1:
                    wachttijd = 15 * (poging + 1)
                    st.info(f"⏳ Google Gemini servers zijn druk of limiet bereikt. Automatische pauze van {wachttijd} seconden...")
                    time.sleep(wachttijd)
                    continue
                else:
                    st.error("⚠️ De limiet voor de Gemini API is tijdelijk bereikt of de servers zijn te druk. Wacht 1-2 minuten.")
            raise e

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
if "onderzoeks_payload" not in st.session_state:
    st.session_state.onderzoeks_payload = None

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE & ZIJBALK
# ------------------------------------------------------------------------------
st.set_page_config(page_title="RBC Archief zoekmachine", page_icon="🔍", layout="wide")

with st.sidebar:
    st.title("ℹ️ Help & Info")
    with st.expander("🚨 Belangrijke informatie & Foutmeldingen"):
        st.markdown("""
        **1. Rood blok met foutmelding (bijv. 429 RESOURCE_EXHAUSTED)?**  
        Deze zoekmachine maakt gebruik van een gratis AI-model met een dagelijks limiet. Krijg je een melding over 'quota'? Probeer het later opnieuw.

        **2. Houd rekening met mogelijke fouten in de AI-analyse**  
        Controleer cruciale informatie altijd in het originele archiefdocument!
        """)
    with st.expander("💡 Tips voor het testen"):
        st.markdown("""
        * **Klikbare Tegels:** Klik op een fototegel om het document te openen.
        * **Bladeren:** Gebruik **`←`** en **`→`** op je toetsenbord.
        * **Sluiten:** Druk op **`ESC`**.
        """)

col_title, col_ver = st.columns([4, 1])
with col_title:
    st.title("🔍 RBC Archief zoekmachine")
with col_ver:
    st.caption(f"**Versie:** `{APP_VERSION}` ({APP_DATE})")

if MODEL_NAAM:
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")
else:
    st.error("Kon geen werkend Gemini-model vinden voor deze API-sleutel.")
    st.stop()

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: Wat staat er in het boek van Mathieu Rutten over de elektriciteitscentrale in Tongeren?',
        height=100
    )
with col2:
    max_dossiers = st.slider("Max dossiers (Document_ID's):", min_value=5, max_value=50, value=10, step=5)

btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

if submit_button:
    st.session_state.gestopt = False
    st.session_state.actieve_chat = None
    st.session_state.chat_historie = []
    st.session_state.blader_paginas = []
    st.session_state.geselecteerde_doc_ids = []
    st.session_state.onderzoeks_payload = None
    st.session_state.start_zoekopdracht = True
    st.rerun()

if stop_button:
    st.session_state.gestopt = True
    st.session_state.start_zoekopdracht = False
    st.warning("⚠️ Onderzoek is direct geannuleerd.")
    st.stop()

# ------------------------------------------------------------------------------
# 4. ONDERZOEKSLOGICA (DOCUMENTEN EN AFBEELDINGEN VERZAMELEN)
# ------------------------------------------------------------------------------
if st.session_state.start_zoekopdracht:
    if not onderzoeksvraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
        st.session_state.start_zoekopdracht = False
    else:
        with st.spinner("Stap 1/2: Inhoudsopgave scannen & documenten zoeken..."):
            try:
                sh = gc.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.session_state.start_zoekopdracht = False
                st.stop()

            if not data:
                st.error("De Google Sheet bevat geen geldige gegevens.")
                st.session_state.start_zoekopdracht = False
                st.stop()

            negeer_woorden = [
                'geef', 'alle', 'over', 'radio', 'model', 'voor', 'naar', 'van', 'informatie', 
                'weet', 'welke', 'zoek', 'vind', 'wat', 'is', 'de', 'het', 'een', 'wanneer', 
                'overleed', 'gestorven', 'waar', 'wie', 'hoe', 'quand', 'où', 'geboren', 'overleden',
                'dossier', 'document', 'archief', 'toon', 'laat', 'zien', 'hebt', 'gehad', 'expliciet',
                'geschreven', 'door', 'staan', 'vertel', 'me'
            ]
            
            schoon_vraag = onderzoeksvraag.translate(str.maketrans('', '', string.punctuation))
            ruwe_woorden = [w.lower() for w in schoon_vraag.split() if len(w) > 2 and w.lower() not in negeer_woorden]
            
            zoek_groepen = []
            for w in ruwe_woorden:
                varianten = [w]
                if w == 'emiel': varianten.append('emile')
                elif w == 'emile': varianten.append('emiel')
                elif w == 'jan': varianten.append('jean')
                elif w == 'jean': varianten.append('jan')
                zoek_groepen.append(varianten)

            doc_scores = {}

            if zoek_groepen:
                familie_termen = ['familie', 'stamboom', 'geslacht', 'ouders', 'kinderen', 'echtgenoot', 'echtgenote', 'huwelijk', 'auteur']
                is_boek_vraag = any(b_woord in onderzoeksvraag.lower() for b_woord in ['boek', 'publicatie', 'omslag', 'band', 'tijdschrift', 'magazine', 'weekblad'])

                for row in data:
                    doc_id_val = str(row.get('Document_ID', '')).strip()
                    b_naam_val = str(row.get('Bestandsnaam', '')).strip()
                    personen_val = str(row.get('Genoemde Personen', '') or row.get('Genoemde personen', '')).strip()
                    onderwerp_val = str(row.get('Onderwerp (NL)', '') or row.get('Onderwerp', '')).strip()
                    inhoud_val = str(row.get('Inhoud & Cijfers (NL)', '') or row.get('Inhoud & cijfers (NL)', '') or row.get('Inhoud', '')).strip()
                    
                    combi_tekst = f"{doc_id_val} {b_naam_val} {personen_val} {onderwerp_val} {inhoud_val}".lower()
                    gekozen_id = doc_id_val if doc_id_val else f"SINGLE_{b_naam_val}"
                    if not gekozen_id: continue

                    matched_groepen_count = sum(1 for grp in zoek_groepen if any(v in combi_tekst for v in grp))
                    score = matched_groepen_count * 5

                    if matched_groepen_count == len(zoek_groepen): score += 10
                    if is_boek_vraag and any(b_term in combi_tekst for b_term in ['boek', 'omslag', 'publicatie', 'tijdschrift', 'weekblad']): score += 25
                    if any(v in combi_tekst for grp in zoek_groepen for v in grp if len(v) > 3) and any(f in combi_tekst for f in familie_termen): score += 8

                    if score > 0:
                        doc_scores[gekozen_id] = doc_scores.get(gekozen_id, 0) + score

                gesorteerde_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
                st.session_state.geselecteerde_doc_ids = [doc_id for doc_id, score in gesorteerde_docs[:max_dossiers]]

        if not st.session_state.geselecteerde_doc_ids:
            st.warning("⚠️ Geen relevante documenten gevonden.")
            st.session_state.start_zoekopdracht = False
            st.stop()

        # Verzamel bestanden uit Drive
        eind_bestanden_lijst = []
        sheet_dossier_data = []

        for row in data:
            doc_id = str(row.get('Document_ID', '')).strip()
            b_naam = str(row.get('Bestandsnaam', '')).strip()
            if any(doc_id.lower() == g_id.lower() or b_naam.lower() == g_id.lower() for g_id in st.session_state.geselecteerde_doc_ids):
                sheet_dossier_data.append(row)
                if b_naam and b_naam not in eind_bestanden_lijst:
                    eind_bestanden_lijst.append(b_naam)

        MAX_FOTO_LIMIET = 10
        payload = [f"Analyseer voor de onderzoeksvraag: {onderzoeksvraag}"]

        if len(eind_bestanden_lijst) > MAX_FOTO_LIMIET:
            with st.spinner(f"Groot dossier ({len(eind_bestanden_lijst)} pagina's) bundelen..."):
                tekst_gebundeld = f"\n--- DOSSIER INHOUD ({len(sheet_dossier_data)} PAGINA'S) ---\n"
                for idx, r in enumerate(sheet_dossier_data, start=1):
                    tekst_gebundeld += f"\n[Pagina {idx}] Bestand: {r.get('Bestandsnaam')} | Doc_ID: {r.get('Document_ID')}\n  - Personen: {r.get('Genoemde Personen', '')}\n  - Inhoud: {r.get('Inhoud & Cijfers (NL)', '')}\n"
                payload.append(tekst_gebundeld)

                blader_lijst = []
                for g_id in st.session_state.geselecteerde_doc_ids:
                    for r in sheet_dossier_data:
                        d_id = str(r.get('Document_ID', '')).strip()
                        b_n = str(r.get('Bestandsnaam', '')).strip()
                        if d_id.lower() == g_id.lower() and b_n:
                            b_schoon = b_n.split('/')[-1]
                            res = drive_service.files().list(q=f"name = '{b_schoon}' and trashed = false", fields='files(id, name, mimeType)').execute().get('files', [])
                            if res:
                                blader_lijst.append({"doc_id": d_id, "naam": res[0]['name'], "id": res[0]['id'], "mime": res[0]['mimeType']})

                st.session_state.blader_paginas = blader_lijst

        else:
            with st.spinner("Stap 2/2: Afbeeldingen ophalen uit Google Drive..."):
                blader_lijst = []
                for b_naam in eind_bestanden_lijst:
                    b_naam_schoon = str(b_naam).strip("'\" ").split('/')[-1]
                    res = drive_service.files().list(q=f"name = '{b_naam_schoon}' and trashed = false", fields='files(id, name, mimeType)').execute().get('files', [])

                    if res:
                        f = res[0]
                        matching_row = next((r for r in sheet_dossier_data if str(r.get('Bestandsnaam', '')).strip() == b_naam), {})
                        doc_id_val = matching_row.get('Document_ID', st.session_state.geselecteerde_doc_ids[0] if st.session_state.geselecteerde_doc_ids else "Dossier 1")

                        blader_lijst.append({"doc_id": doc_id_val, "naam": f['name'], "id": f['id'], "mime": f['mimeType']})

                        req = drive_service.files().get_media(fileId=f['id'])
                        f_data = req.execute()
                        img = Image.open(io.BytesIO(f_data)).convert('RGB')
                        
                        img.thumbnail((600, 600))
                        img_byte_arr = io.BytesIO()
                        img.save(img_byte_arr, format='JPEG', quality=60)

                        payload.append(types.Part.from_bytes(data=img_byte_arr.getvalue(), mime_type='image/jpeg'))

                st.session_state.blader_paginas = blader_lijst

        st.session_state.onderzoeks_payload = payload
        st.session_state.start_zoekopdracht = False
        st.rerun()

# ------------------------------------------------------------------------------
# 5. KLIKBARE FOTOTEGELS (WORDT DIRECT AFGEBEELD)
# ------------------------------------------------------------------------------
if st.session_state.blader_paginas:
    st.divider()
    
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
                eerste_pagina["display_label"] = f"{d_id} ({aantal_pags} pag.)"
            else:
                eerste_pagina["display_label"] = d_id
                
            tegel_items.append(eerste_pagina)

    st.subheader(f"🖼️ Geselecteerde Archiefdocumenten ({len(tegel_items)} dossiers • {len(st.session_state.blader_paginas)} pagina's)")
    st.caption("Klik op een tegel om het hele document/boek te openen in de viewer.")

    tegels_json = json.dumps(tegel_items)
    alle_dossiers_json = json.dumps(dossiers_dict)

    grid_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body {{
                margin: 0;
                padding: 10px 0;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                background: transparent;
            }}
            .grid-container {{
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
                gap: 15px;
                width: 100%;
            }}
            .tile {{
                background: #ffffff;
                border: 1px solid #e0e0e0;
                border-radius: 8px;
                overflow: hidden;
                cursor: pointer;
                transition: transform 0.2s, box-shadow 0.2s;
                display: flex;
                flex-direction: column;
                align-items: center;
                box-shadow: 0 2px 5px rgba(0,0,0,0.05);
            }}
            .tile:hover {{
                transform: translateY(-3px);
                box-shadow: 0 6px 15px rgba(0,0,0,0.15);
                border-color: #1a73e8;
            }}
            .img-container {{
                width: 100%;
                height: 180px;
                background-color: #f5f5f5;
                display: flex;
                align-items: center;
                justify-content: center;
                overflow: hidden;
            }}
            .img-container img {{
                width: 100%;
                height: 100%;
                object-fit: cover;
            }}
            .tile-caption {{
                padding: 10px 8px;
                font-size: 12px;
                font-weight: 600;
                color: #202124;
                text-align: center;
                word-break: break-word;
                line-height: 1.3;
                width: 100%;
                box-sizing: border-box;
                background: #fafafa;
                border-top: 1px solid #f0f0f0;
            }}
        </style>
    </head>
    <body>

        <div class="grid-container" id="tile-grid"></div>

        <script>
            const tegels = {tegels_json};
            const alleDossiers = {alle_dossiers_json};

            function renderTiles() {{
                const grid = document.getElementById('tile-grid');
                grid.innerHTML = '';

                tegels.forEach((item) => {{
                    const tile = document.createElement('div');
                    tile.className = 'tile';
                    tile.onclick = () => openDriveOverlay(item.doc_id);

                    const imgUrl = `https://lh3.googleusercontent.com/d/${{item.id}}`;
                    const labelTekst = item.display_label || item.doc_id || item.naam;

                    tile.innerHTML = `
                        <div class="img-container">
                            <img src="${{imgUrl}}" loading="lazy" alt="${{labelTekst}}" />
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

                const bestaandeModal = topDoc.getElementById('rbc-drive-modal');
                if (bestaandeModal) bestaandeModal.remove();

                const modal = topDoc.createElement('div');
                modal.id = 'rbc-drive-modal';
                modal.style.cssText = `
                    position: fixed;
                    top: 0;
                    left: 0;
                    width: 100vw;
                    height: 100vh;
                    background-color: rgba(0, 0, 0, 0.92);
                    backdrop-filter: blur(6px);
                    z-index: 9999999;
                    display: flex;
                    flex-direction: column;
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
                `;

                modal.innerHTML = `
                    <div id="rbc-top-bar" style="height: 56px; background: rgba(20,20,20,0.95); display: flex; align-items: center; padding: 0 20px; color: white; border-bottom: 1px solid rgba(255,255,255,0.1);">
                        <button id="rbc-close-btn" style="background: transparent; border: none; color: white; font-size: 24px; cursor: pointer; width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; margin-right: 15px;" title="Sluiten (ESC)">✕</button>
                        <div id="rbc-title-info" style="font-size: 15px; color: #e8eaed; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">Laden...</div>
                    </div>
                    <div style="position: relative; flex: 1; width: 100%; height: calc(100vh - 56px); display: flex; align-items: center; justify-content: center; overflow: hidden;">
                        <img id="rbc-img" style="max-width: 90%; max-height: 90%; object-fit: contain; border-radius: 4px; box-shadow: 0 0 25px rgba(0,0,0,0.8); transition: opacity 0.2s;" src="" />
                        <div id="rbc-prev-btn" style="position: absolute; top: 50%; left: 20px; transform: translateY(-50%); width: 48px; height: 48px; background: rgba(30,30,30,0.8); color: white; border: 1px solid rgba(255,255,255,0.2); border-radius: 50%; font-size: 28px; display: flex; align-items: center; justify-content: center; cursor: pointer; user-select: none; z-index: 10;" title="Vorige pagina">&#8249;</div>
                        <div id="rbc-next-btn" style="position: absolute; top: 50%; right: 20px; transform: translateY(-50%); width: 48px; height: 48px; background: rgba(30,30,30,0.8); color: white; border: 1px solid rgba(255,255,255,0.2); border-radius: 50%; font-size: 28px; display: flex; align-items: center; justify-content: center; cursor: pointer; user-select: none; z-index: 10;" title="Volgende pagina">&#8250;</div>
                    </div>
                `;

                topDoc.body.appendChild(modal);
                topDoc.body.style.overflow = 'hidden';

                const imgEl = topDoc.getElementById('rbc-img');
                const titleInfo = topDoc.getElementById('rbc-title-info');
                const closeBtn = topDoc.getElementById('rbc-close-btn');
                const prevBtn = topDoc.getElementById('rbc-prev-btn');
                const nextBtn = topDoc.getElementById('rbc-next-btn');

                function updateViewer() {{
                    if (dossierPaginas.length === 0) return;
                    const item = dossierPaginas[currentIndex];
                    
                    imgEl.style.opacity = '0.3';
                    imgEl.src = `https://lh3.googleusercontent.com/d/${{item.id}}`;
                    imgEl.onload = () => {{ imgEl.style.opacity = '1'; }};

                    titleInfo.innerText = `${{item.naam}}  •  Pagina ${{currentIndex + 1}} van ${{dossierPaginas.length}}`;

                    prevBtn.style.opacity = (currentIndex === 0) ? '0.2' : '1';
                    prevBtn.style.pointerEvents = (currentIndex === 0) ? 'none' : 'auto';

                    nextBtn.style.opacity = (currentIndex === dossierPaginas.length - 1) ? '0.2' : '1';
                    nextBtn.style.pointerEvents = (currentIndex === dossierPaginas.length - 1) ? 'none' : 'auto';
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

                closeBtn.onclick = sluitModal;
                prevBtn.onclick = () => {{ if (currentIndex > 0) {{ currentIndex--; updateViewer(); }} }};
                nextBtn.onclick = () => {{ if (currentIndex < dossierPaginas.length - 1) {{ currentIndex++; updateViewer(); }} }};

                topDoc.addEventListener('keydown', keyHandler);

                updateViewer();
            }}

            renderTiles();
        </script>
    </body>
    </html>
    """

    aantal_tegels = len(tegel_items)
    berekende_hoogte = max(260, ((aantal_tegels // 5) + 1) * 250)
    components.html(grid_html, height=berekende_hoogte, scrolling=True)

# ------------------------------------------------------------------------------
# 6. HISTORISCH RAPPORT & CHAT (VOERT NÁ HET TONEN VAN DE TEGELS DE GEMINI-ANALYSE UIT)
# ------------------------------------------------------------------------------
if st.session_state.onderzoeks_payload and not st.session_state.chat_historie:
    with st.spinner("Historische analyse uitvoeren via Gemini..."):
        try:
            st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
            analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, st.session_state.onderzoeks_payload)
            st.session_state.chat_historie.append(("assistant", analyse_response.text))
            st.session_state.onderzoeks_payload = None
            st.rerun()
        except Exception as e:
            st.error(f"Fout tijdens analyse: {e}")

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
                    st.error(f"Fout bij verwerken vervolgvraag: {e}")
