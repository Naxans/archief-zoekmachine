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
# 2. HELPER FUNCTIES VOOR PAGINA-KOPPELING EN MODEL-DETECTIE
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

def bepaal_werkend_model(client):
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

def voeg_volgpaginas_toe(geselecteerde_lijst, alle_bestanden_sheet):
    """
    Scant de geselecteerde bestanden. Als 'blz2547' erin staat,
    zoekt Python automatisch in de Sheet naar 'blz2548' en voegt deze toe.
    """
    resultaat = list(geselecteerde_lijst)
    
    for b_naam in geselecteerde_lijst:
        # Zoek naar getallen in de bestandsnaam (bijv. paginanummers zoals 2547 of page1)
        nummers = re.findall(r'\d+', b_naam)
        if nummers:
            laatste_num = nummers[-1]
            volgend_num = str(int(laatste_num) + 1)
            
            # Vervang het nummer door het volgende nummer
            volgende_naam_gok = b_naam.replace(laatste_num, volgend_num)
            
            # Controleer of dit bestand daadwerkelijk bestaat in de Google Sheet
            for kandidaat in alle_bestanden_sheet:
                if volgende_naam_gok.lower() in kandidaat.lower() and kandidaat not in resultaat:
                    resultaat.append(kandidaat)
                    
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
    st.error("Kon geen werkend Gemini-model vinden. Controleer je API Key.")
    st.stop()

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: Geef me de bestuursleden van de firma "Radio Belge de Construction" in het jaar 1935',
        height=100
    )
with col2:
    max_bestanden = st.slider("Max bronnen:", min_value=5, max_value=50, value=15, step=5)

btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

if stop_button:
    st.session_state.gestopt = True
    st.warning("⚠️ Onderzoek geannuleerd.")
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
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.stop()

            if not data:
                st.error("De Google Sheet bevat geen geldige gegevens.")
                st.stop()

            alle_sheet_bestanden = [str(r.get('Bestandsnaam', '')).strip() for r in data]

            index_regels = []
            for row in data:
                b_naam_val = row.get('Bestandsnaam') or row.get('Bestandsnaam (ID)') or ''
                regel = f"Bestandsnaam: {b_naam_val} | Datum: {row.get('Datum Document')} | Personen: {row.get('Genoemde Personen')} | Onderwerp: {row.get('Onderwerp (NL)')}"
                index_regels.append(regel)

            index_tekst = "\n".join(index_regels)
            if len(index_tekst) > 250000:
                index_tekst = index_tekst[:250000]

            # Ruime filter-prompt zodat Gemini NIET te streng bestanden wegfiltert
            filter_prompt = f"""
            Hier is de inhoudsopgave van ons archief:

            {index_tekst}

            ZOEKOPDRACHT: "{onderzoeksvraag}"

            OPDRACHT:
            1. Geef ALLE bestandsnamen terug die te maken hebben met de firma, personen, onderwerpen of jaartallen uit de zoekopdracht.
            2. Bij twijfel: SLUIT HET BESTAND IN. Wees niet te streng, beter 1 bestand te veel dan te weinig.
            3. Selecteer maximaal {max_bestanden} bestandsnamen.

            Geef ENKEL de exacte bestandsnamen terug, gescheiden door komma's. Geeft geen andere tekst.
            """

            try:
                res_filter = genereer_met_retry(ai_client, MODEL_NAAM, filter_prompt)
                geselecteerde_bestanden = [b.strip() for b in res_filter.text.split(',') if b.strip()]
            except Exception as e:
                st.error(f"Fout tijdens het scannen van de index: {e}")
                st.stop()

            # Automatische verrijking: Python zoekt direct naar vervolgpagina's
            geselecteerde_bestanden = voeg_volgpaginas_toe(geselecteerde_bestanden, alle_sheet_bestanden)

        if not geselecteerde_bestanden:
            st.warning("Geen relevante bestanden gevonden.")
            st.stop()

        # STAP 2: Documenten ophalen uit Drive
        with st.spinner(f"Stap 2/3: {len(geselecteerde_bestanden)} document(en) ophalen uit Google Drive..."):
            onderzoeks_payload = [
                f"""Jij bent een financieel-historisch expert en hoofdarchivaris.
Beantwoord de onderzoeksvraag uitsluitend en uiterst nauwkeurig op basis van de meegeleverde documenten.

ONDERZOEKSVRAAG: {onderzoeksvraag}

INSTRUCTIES OM FOUTEN EN VERWARRING TE VOORKOMEN:

1. KRITISCH LEZEN VAN STUKKEN / STAATSBLADEN:
   - Op één pagina staan vaak meerdere publicaties van VERSCHILLENDE bedrijven.
   - Wijs bestuursleden/functies ALLEEN toe aan de gezochte firma als de naam ONDER het kopje/titel van die specifieke firma staat!
   - Neem GEEN namen over die onder een ander bedrijf op dezelfde pagina staan.

2. MULTI-PAGINA DOCUMENTEN:
   - Lees vervolgpagina's goed door. Als de akte van de gezochte firma doorloopt op een volgende pagina, horen de namen op die vervolgpagina bij deze firma.

3. RAPPORTAGE:
   - Noem per gevonden persoon de exacte functie en de geciteerde bestandsnaam (bijv. 'staatsblad1935-blz2.jpg').
   - Als er sprake is van een bestuurswijziging in de loop der tijd, vermeld dit dan chronologisch.
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
                            pdf_part = types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf')
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

        if geladen_aantal == 0:
            st.error("Geen van de documenten kon uit Google Drive worden opgehaald.")
            st.stop()

        # STAP 3: AI-analyse
        with st.spinner("Stap 3/3: Dokumenten analyseren met Gemini..."):
            if st.session_state.gestopt:
                st.warning("Onderzoek geannuleerd.")
                st.stop()

            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = st.session_state.actieve_chat.send_message(onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
            except Exception as e:
                st.error(f"Fout tijdens Gemini analyse: {e}")

# ------------------------------------------------------------------------------
# 5. WEERGAVE BRONNEN & RESULTATEN
# ------------------------------------------------------------------------------
if st.session_state.bron_details:
    st.subheader(f"📁 Geanalyseerde bronnen ({len(st.session_state.bron_details)}):")
    
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
