import io
import re
import time
import logging
import warnings
import streamlit as st
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google import genai
from google.genai import types

# SDK meldingen onderdrukken voor schone logs
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
# 2. HELPER FUNCTIES (MODEL DETECTIE & DYNAMISCHE PAGINA-KOPPELING)
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

def bepaal_werkend_model(client):
    """Vraagt actieve modellen op bij Google en test welke daadwerkelijk werkt."""
    kandidaten = [
        'gemini-2.0-flash',
        'gemini-2.0-flash-lite',
        'gemini-1.5-flash-002',
        'gemini-1.5-pro-002'
    ]
    
    try:
        voorradig = [m.name.replace("models/", "") for m in client.models.list()]
        for m in voorradig:
            if m not in kandidaten and 'gemini' in m:
                kandidaten.append(m)
    except Exception:
        pass

    for model_naam in kandidaten:
        try:
            client.models.generate_content(model=model_naam, contents="ping")
            return model_naam
        except Exception:
            continue

    return None

MODEL_NAAM = bepaal_werkend_model(ai_client)

def genereer_met_retry(client, model, contents, max_retries=3):
    """Voert een API-call uit en wacht automatisch als de TPM-limiet (429) bereikt wordt."""
    for poging in range(max_retries):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if poging < max_retries - 1:
                    time.sleep(12)
                    continue
            raise e

def voeg_vervolgpaginas_toe(geselecteerde_bestanden, alle_sheet_bestanden):
    """
    Koppelt automatisch de direct opvolgende pagina (rij N+1) uit de Google Sheet.
    Dit zorgt ervoor dat doorgelopen akten in staatsbladen/kranten nooit worden afgesneden.
    """
    resultaat = list(geselecteerde_bestanden)
    
    for b_naam in geselecteerde_bestanden:
        if b_naam in alle_sheet_bestanden:
            idx = alle_sheet_bestanden.index(b_naam)
            # Voeg de direct volgende pagina toe als die bestaat
            if idx + 1 < len(alle_sheet_bestanden):
                volgende_pagina = alle_sheet_bestanden[idx + 1]
                if volgende_pagina not in resultaat:
                    resultaat.append(volgende_pagina)
                    
    return resultaat

# Session state variabelen
if "actieve_chat" not in st.session_state:
    st.session_state.actieve_chat = None
if "chat_historie" not in st.session_state:
    st.session_state.chat_historie = []
if "bron_details" not in st.session_state:
    st.session_state.bron_details = []
if "gestopt" not in st.session_state:
    st.session_state.gestopt = False

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE
# ------------------------------------------------------------------------------
st.set_page_config(page_title="Archief Zoekmachine", page_icon="🔍", layout="wide")
st.title("🔍 Archief Zoekmachine")

if MODEL_NAAM:
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")
else:
    st.error("Kon geen werkend Gemini-model vinden voor deze API-sleutel. Controleer je Gemini API key.")
    st.stop()

# Invoer van de onderzoeksvraag & parameters
col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: Geef me de bestuursleden van de firma "Radio Belge de Construction" in het jaar 1935',
        height=100
    )
with col2:
    max_bestanden = st.slider("Max bronnen:", min_value=5, max_value=50, value=15, step=5)

# Knoppenbalk met Actie- en Stop-knoppen
btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

# Directe stop-afhandeling
if stop_button:
    st.session_state.gestopt = True
    st.warning("⚠️ Onderzoek is direct geannuleerd.")
    st.stop()

# ------------------------------------------------------------------------------
# 4. ONDERZOEKSLOGICA
# ------------------------------------------------------------------------------
if submit_button:
    if not onderzoeksvraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
    else:
        st.session_state.gestopt = False
        st.session_state.chat_historie = []
        st.session_state.bron_details = []
        
        # STAP 1: Inhoudsopgave scannen
        with st.spinner("Stap 1/3: Inhoudsopgave (Google Sheet) scannen..."):
            try:
                sh = gc.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                
                # Filter lege rijen uit
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.stop()

            if not data:
                st.error("De Google Sheet bevat geen geldige gegevens.")
                st.stop()

            if st.session_state.gestopt:
                st.stop()

            # Bewaar de exacte volgorde van bestandsnamen in de Google Sheet
            alle_sheet_bestanden = [str(row.get('Bestandsnaam', '')).strip() for row in data]

            # Directe bestandsnaam check
            geselecteerde_bestanden = []
            for b_naam_sheet in alle_sheet_bestanden:
                if b_naam_sheet and b_naam_sheet.lower() in onderzoeksvraag.lower():
                    geselecteerde_bestanden.append(b_naam_sheet)

            # Zoek via Gemini als er geen directe naam match is
            if not geselecteerde_bestanden:
                index_regels = []
                for row in data:
                    b_naam_val = row.get('Bestandsnaam') or row.get('Bestandsnaam (ID)') or ''
                    regel = f"Bestandsnaam: {b_naam_val} | Datum: {row.get('Datum Document')} | Personen: {row.get('Genoemde Personen')} | Onderwerp: {row.get('Onderwerp (NL)')}"
                    index_regels.append(regel)

                index_tekst = "\n".join(index_regels)
                
                if len(index_tekst) > 250000:
                    index_tekst = index_tekst[:250000]

                filter_prompt = f"""
                Jij bent hoofdarchivaris. Hieronder staat de volledige inhoudsopgave van ons archief:

                {index_tekst}

                ONDERZOEKSVRAAG: "{onderzoeksvraag}"

                INSTRUCTIES:
                1. Selecteer ALLE bestanden uit de index die te maken hebben met het bedrijf, de personen of het jaar uit de vraag.
                2. Beter 1 bestand te veel geselecteerd dan 1 te weinig.
                3. Geef maximaal {max_bestanden} meest relevante bestandsnamen terug.

                Geef UITSLUITEND de exacte bestandsnamen terug gescheiden door komma's. Geen extra tekst.
                """

                try:
                    res_filter = genereer_met_retry(ai_client, MODEL_NAAM, filter_prompt)
                    geselecteerde_bestanden = [b.strip() for b in res_filter.text.split(',') if b.strip()]
                except Exception as e:
                    st.error(f"Fout tijdens het scannen van de index ({MODEL_NAAM}): {e}")
                    st.stop()

            # AUTOMATISCHE KOPPELING: Voeg voor elke geselecteerde pagina direct de VOLGENDE pagina toe
            geselecteerde_bestanden = voeg_vervolgpaginas_toe(geselecteerde_bestanden, alle_sheet_bestanden)

        if not geselecteerde_bestanden:
            st.warning("Geen relevante bestanden gevonden op basis van de zoekopdracht.")
            st.stop()

        # STAP 2: Originele documenten ophalen uit Google Drive
        with st.spinner(f"Stap 2/3: Originele documenten ({len(geselecteerde_bestanden)} stuks) ophalen uit Drive..."):
            onderzoeks_payload = [
                f"""Jij bent een financieel-historisch expert en archivaris.
Beantwoord de onderzoeksvraag uiterst nauwkeurig op basis van de meegeleverde documenten en/of afbeeldingen.

ONDERZOEKSVRAAG: {onderzoeksvraag}

CRUCIALE INSTRUCTIES VOOR HERKENNING EN VERVOLGPAGINA'S:
1. DOORGELOPEN AKTEN / VERVOLGPAGINA'S:
   - De meegeleverde bestanden staan in chronologische/volgordelijke reeksen.
   - Een akte van een bedrijf (zoals "Radio Belge de Construction") begint vaak onderaan pagina 1 en loopt bovenaan pagina 2 door.
   - Lees vervolgpagina's direct in samenhang met de voorafgaande pagina. De namen bovenaan pagina 2 horen bij het bedrijf dat onderaan pagina 1 werd aangekondigd!

2. STRIKTE TOEWIJZING VAN PERSONEN:
   - Wijs personen/bestuursleden ALLEEN toe aan de gezochte firma als de tekst dit expliciet bevestigt.
   - Voorkom verwarring met andere bedrijven die toevallig op dezelfde bladzijde staan afgedrukt.

3. RAPPORTAGE:
   - Geef een helder, chronologisch overzicht van alle vastgestelde bestuursleden en hun functies.
   - Vermeld steeds de exacte bestandsnaam (bijv. 'staatsblad1935_blz2548.jpg') waarin elke specifieke naam/functie is teruggevonden.
"""
            ]

            geladen_aantal = 0
            for b_naam in geselecteerde_bestanden:
                if st.session_state.gestopt:
                    st.warning("Onderzoek geannuleerd bij het ophalen van bestanden.")
                    st.stop()

                b_naam_schoon = b_naam.strip("'\" ")
                if ":" in b_naam_schoon:
                    b_naam_schoon = b_naam_schoon.split(":", 1)[-1].strip()
                
                query = f"name = '{b_naam_schoon}' and trashed = false"
                res = drive_service.files().list(q=query, fields='files(id, name, mimeType)').execute()
                bestanden = res.get('files', [])

                if not bestanden:
                    schoon_zonder_ext = b_naam_schoon.replace('.jpg', '').replace('.JPG', '').replace('.jpeg', '').replace('.png', '').replace('.pdf', '')
                    query_flexibel = f"name contains '{schoon_zonder_ext}' and trashed = false"
                    res = drive_service.files().list(q=query_flexibel, fields='files(id, name, mimeType)').execute()
                    bestanden = res.get('files', [])

                if bestanden:
                    f = bestanden[0]
                    b_id = f['id']
                    b_mime = f['mimeType']
                    b_real_naam = f['name']

                    st.session_state.bron_details.append({
                        "naam": b_real_naam,
                        "id": b_id,
                        "mime": b_mime
                    })

                    try:
                        if b_mime == 'application/vnd.google-apps.document':
                            req = drive_service.files().export_media(fileId=b_id, mimeType='text/plain')
                            doc_txt = req.execute().decode('utf-8', errors='ignore')
                            onderzoeks_payload.append(f"\n--- INHOUD GOOGLE DOC ({b_real_naam}) ---\n{doc_txt}")
                        
                        elif b_mime == 'application/pdf' or b_real_naam.lower().endswith('.pdf'):
                            req = drive_service.files().get_media(fileId=b_id)
                            pdf_bytes = req.execute()

                            pdf_part = types.Part.from_bytes(
                                data=pdf_bytes,
                                mime_type='application/pdf'
                            )
                            onderzoeks_payload.append(f"\n--- ORIGINELE PDF: {b_real_naam} ---")
                            onderzoeks_payload.append(pdf_part)

                        else:
                            req = drive_service.files().get_media(fileId=b_id)
                            f_data = req.execute()

                            img = Image.open(io.BytesIO(f_data))
                            if img.mode != 'RGB':
                                img = img.convert('RGB')
                            
                            img.thumbnail((800, 800))

                            img_byte_arr = io.BytesIO()
                            img.save(img_byte_arr, format='JPEG', quality=70)

                            img_part = types.Part.from_bytes(
                                data=img_byte_arr.getvalue(),
                                mime_type='image/jpeg'
                            )
                            onderzoeks_payload.append(f"\n--- ORIGINELE AFBEELDING: {b_real_naam} ---")
                            onderzoeks_payload.append(img_part)

                        geladen_aantal += 1
                    except Exception as e:
                        st.warning(f"Kon {b_real_naam} niet laden: {e}")
                else:
                    st.warning(f"Bestand '{b_naam_schoon}' niet gevonden in Google Drive.")

        if geladen_aantal == 0:
            st.error("De geselecteerde bestanden konden niet worden teruggevonden in Google Drive.")
            st.stop()

        # STAP 3: Analyse uitvoeren via Gemini
        with st.spinner("Stap 3/3: Analyse uitvoeren via Gemini..."):
            if st.session_state.gestopt:
                st.warning("Onderzoek geannuleerd voor de AI-analyse.")
                st.stop()

            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = st.session_state.actieve_chat.send_message(onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
            except Exception as e:
                st.error(f"Fout tijdens Gemini analyse: {e}")
                st.info("💡 Tip: Probeer 'Max bronnen' te verlagen naar bijv. 5 bronnen om binnen de limieten te blijven.")

# ------------------------------------------------------------------------------
# 5. WEERGAVE BRONNEN MET PREVIEWS & RAPPORT
# ------------------------------------------------------------------------------
if st.session_state.bron_details:
    st.subheader("📁 Geselecteerde bronnen & Afbeeldingen:")
    
    cols = st.columns(3)
    for index, bron in enumerate(st.session_state.bron_details):
        b_naam = bron["naam"]
        b_id = bron["id"]
        
        thumbnail_url = f"https://drive.google.com/thumbnail?id={b_id}&sz=w800"
        drive_view_url = f"https://drive.google.com/file/d/{b_id}/view"

        with cols[index % 3]:
            with st.expander(f"📄 {b_naam}", expanded=True):
                st.image(thumbnail_url, caption=b_naam, use_container_width=True)
                st.link_button("🔍 Open in hoge resolutie", drive_view_url)

if st.session_state.chat_historie:
    st.divider()
    st.subheader("📑 Historisch Onderzoeksrapport")
    
    for rol, tekst in st.session_state.chat_historie:
        with st.chat_message(rol):
            st.write(tekst)

    # Vervolgvragen stellen
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
