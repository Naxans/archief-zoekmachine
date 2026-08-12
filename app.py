import io
import time
import streamlit as st
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google import genai
from google.genai import types

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
# 2. CONFIGURATIE & MODEL-DETECTIE
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

@st.cache_resource
def bepaal_werkend_model(_client):
    kandidaten = [
        'gemini-1.5-flash',
        'gemini-2.0-flash',
        'gemini-1.5-pro',
        'gemini-2.0-flash-lite'
    ]
    for model_naam in kandidaten:
        try:
            _client.models.generate_content(model=model_naam, contents="ping")
            return model_naam
        except Exception:
            continue
    return 'gemini-1.5-flash'

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
                    time.sleep(12)  # Wacht 12 seconden voor de volgende poging
                    continue
            raise e

# Session state voor chatsessie
if "actieve_chat" not in st.session_state:
    st.session_state.actieve_chat = None
if "chat_historie" not in st.session_state:
    st.session_state.chat_historie = []

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE
# ------------------------------------------------------------------------------
st.set_page_config(page_title="Archief Zoekmachine", page_icon="🔍", layout="wide")
st.title("🔍 Archief Zoekmachine")
st.caption(f"Actief AI-model: `{MODEL_NAAM}`")

# Invoerformulier
with st.form(key="onderzoek_form"):
    col1, col2 = st.columns([3, 1])
    with col1:
        onderzoeksvraag = st.text_input(
            "Vraag:",
            placeholder='Bijv: Geef me de bestuursleden van de firma "Radio Belge de Construction" in het jaar 1935'
        )
    with col2:
        max_bestanden = st.slider("Max bronnen:", min_value=5, max_value=50, value=15, step=5)
    
    submit_button = st.form_submit_button(label="Voer onderzoek uit", type="primary")

# ------------------------------------------------------------------------------
# 4. ONDERZOEKSLOGICA
# ------------------------------------------------------------------------------
if submit_button:
    if not onderzoeksvraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
    else:
        st.session_state.chat_historie = []
        
        # STAP 1: Inhoudsopgave scannen
        with st.spinner("Stap 1/3: Inhoudsopgave (Google Sheet) scannen..."):
            try:
                sh = gc.open(SHEET_NAAM)
                worksheet = sh.sheet1
                data = worksheet.get_all_records()
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.stop()

            if not data:
                st.error("De Google Sheet bevat nog geen data of is leeg.")
                st.stop()

            index_regels = []
            for row in data:
                regel = f"B:{row.get('Bestandsnaam')} | D:{row.get('Datum Document')} | P:{row.get('Genoemde Personen')} | O:{row.get('Onderwerp (NL)')}"
                index_regels.append(regel)

            index_tekst = "\n".join(index_regels)
            
            # Voorkom dat de prompt langer is dan ~40.000 tekens (ca. 10.000 tokens)
            if len(index_tekst) > 40000:
                index_tekst = index_tekst[:40000]

            filter_prompt = f"""
            Jij bent hoofdarchivaris. Hieronder staat de inhoudsopgave van ons archief:

            {index_tekst}

            ONDERZOEKSVRAAG: "{onderzoeksvraag}"

            INSTRUCTIES:
            1. Welke bestanden/foto's uit de index zijn het meest relevant voor deze specifieke vraag?
            2. Let goed op het BEDRIJF, PERSONEN en PERIODE/JAARTALLEN.
            3. Geef maximaal {max_bestanden} meest relevante bestandsnamen terug.

            Geef UITSLAUITEND de bestandsnamen terug gescheiden door komma's. Geen extra tekst.
            """

            try:
                res_filter = genereer_met_retry(ai_client, MODEL_NAAM, filter_prompt)
                geselecteerde_bestanden = [b.strip() for b in res_filter.text.split(',') if b.strip()]
            except Exception as e:
                st.error(f"Fout tijdens het scannen van de index: {e}")
                st.info("💡 De API is momenteel druk bezet. Wacht circa 30 seconden en probeer het nogmaals.")
                st.stop()

        st.subheader("Geselecteerde bronnen:")
        st.write(geselecteerde_bestanden)

        if not geselecteerde_bestanden:
            st.warning("Geen relevante bestanden gevonden op basis van de zoekopdracht.")
            st.stop()

        # STAP 2: Originele documenten ophalen
        with st.spinner("Stap 2/3: Originele documenten ophalen uit Google Drive..."):
            alle_drive_bestanden = {}
            page_token = None
            while True:
                res = drive_service.files().list(
                    q="trashed = false",
                    spaces='drive',
                    pageToken=page_token,
                    fields='nextPageToken, files(id, name, mimeType)'
                ).execute()
                for f in res.get('files', []):
                    alle_drive_bestanden[f['name']] = f
                page_token = res.get('nextPageToken')
                if not page_token:
                    break

            onderzoeks_payload = [
                f"""Jij bent een financieel-historisch expert en archivaris.
Beantwoord onderstaande onderzoeksvraag grondig en gedetailleerd op basis van de meegeleverde originele archiefstukken.

ONDERZOEKSVRAAG: {onderzoeksvraag}

INSTRUCTIES VOOR JE RAPPORT:
1. Richt je specifiek op de gevraagde firma, personen en periode.
2. Structureer je antwoord helder.
3. Vermeld alle concrete namen, functies, cijfers en details die op de documenten staan.
4. Citeer steeds de bestandsnaam wanneer je naar specifieke informatie verwijst.
5. Trek een heldere conclusie als antwoord op de vraag.
"""
            ]

            geladen_aantal = 0
            for b_naam in geselecteerde_bestanden:
                if b_naam in alle_drive_bestanden:
                    f = alle_drive_bestanden[b_naam]
                    b_id = f['id']
                    b_mime = f['mimeType']

                    try:
                        if b_mime == 'application/vnd.google-apps.document':
                            req = drive_service.files().export_media(fileId=b_id, mimeType='text/plain')
                            doc_txt = req.execute().decode('utf-8', errors='ignore')
                            onderzoeks_payload.append(f"\n--- INHOUD GOOGLE DOC ({b_naam}) ---\n{doc_txt}")
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
                            onderzoeks_payload.append(f"\n--- ORIGINELE AFBEELDING: {b_naam} ---")
                            onderzoeks_payload.append(img_part)

                        geladen_aantal += 1
                    except Exception as e:
                        st.warning(f"Kon {b_naam} niet laden: {e}")

        if geladen_aantal == 0:
            st.error("De geselecteerde bestanden konden niet worden teruggevonden in Google Drive.")
            st.stop()

        # STAP 3: Analyse uitvoeren
        with st.spinner("Stap 3/3: Analyse uitvoeren via Gemini..."):
            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = st.session_state.actieve_chat.send_message(onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
            except Exception as e:
                st.error(f"Fout tijdens Gemini analyse: {e}")
                st.info("💡 Tip: Probeer 'Max bronnen' te verlagen naar bijvoorbeeld 3 tot 5 bronnen om binnen de limieten te blijven.")

# ------------------------------------------------------------------------------
# 5. WEERGAVE RESULTAAT & CHAT
# ------------------------------------------------------------------------------
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
