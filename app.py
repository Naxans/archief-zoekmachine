# ==============================================================================
# STREAMLIT ARCHIEF-ZOEKMACHINE (GITHUB / STREAMLIT CLOUD)
# ==============================================================================

import io
import time
import logging
import warnings
from PIL import Image
import streamlit as st
import gspread
from google.oauth2 import service_account
from googleapiclient.discovery import build
from google import genai
from google.genai import types

# Silencer voor SDK-waarschuwingen
logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# PAGINA CONFIGURATIE
st.set_page_config(
    page_title="Archief Zoekmachine",
    page_icon="📜",
    layout="wide"
)

st.title("📜 Historisch Archief Onderzoek")

# CONFIGURATIE SIDESCREEN / SECRETS
st.sidebar.header("⚙️ Instellingen & Sleutels")

# Gemini API Key ophalen uit st.secrets of invoerveld
api_key = st.secrets.get("GEMINI_API_KEY", None)
if not api_key:
    api_key = st.sidebar.text_input("Plak je Gemini API Key:", type="password")

if not api_key:
    st.warning("⚠️ Voer a.u.b. een Gemini API Key in via de zijbalk of st.secrets om te starten.")
    st.stop()

# Service Account voor Google Drive & Sheets ophalen
try:
    if "gcp_service_account" in st.secrets:
        creds_dict = dict(st.secrets["gcp_service_account"])
        creds = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=[
                'https://www.googleapis.com/auth/drive.readonly',
                'https://www.googleapis.com/auth/spreadsheets.readonly'
            ]
        )
    else:
        st.error("❌ Geen Google Service Account referenties gevonden in Streamlit Secrets (`gcp_service_account`).")
        st.stop()
except Exception as e:
    st.error(f"❌ Fout bij authenticatie met Google Services: {e}")
    st.stop()

# INITIALISATIE CLIENTS
ai_client = genai.Client(api_key=api_key)
drive_service = build('drive', 'v3', credentials=creds)
gc = gspread.authorize(creds)

DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

# MODEL DETECTOR
@st.cache_resource
def bepaal_actief_model(_client):
    kandidaten = [
        'gemini-2.0-flash-lite',
        'gemini-2.0-flash',
        'gemini-1.5-flash-002',
        'gemini-1.5-flash',
        'gemini-1.5-pro-002'
    ]
    for m in kandidaten:
        try:
            _client.models.generate_content(model=m, contents="ping")
            return m
        except Exception:
            continue
    return 'gemini-2.0-flash-lite'

MODEL_NAAM = bepaal_actief_model(ai_client)
st.sidebar.success(f"✓ AI-Model: `{MODEL_NAAM}`")

# RETRY HULPFUNCTIE
def stuur_met_retry(chat_or_client, contents, is_chat=False, max_retries=3):
    for poging in range(max_retries):
        try:
            if is_chat:
                return chat_or_client.send_message(contents)
            else:
                return chat_or_client.models.generate_content(model=MODEL_NAAM, contents=contents)
        except Exception as e:
            err_msg = str(e)
            if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                wait_time = (poging + 1) * 8
                st.warning(f"⚠️ API limiet bereikt. Wachten voor {wait_time} seconden...")
                time.sleep(wait_time)
            else:
                raise e
    raise Exception("Max retries overschreden wegens API limieten.")

# INVOERFORMULIER
with st.form("zoek_formulier"):
    onderzoeksvraag = st.text_input(
        "Onderzoeksvraag:",
        placeholder="Bijv: Geef me de bestuursleden van Radio Belge de Construction in 1936"
    )
    max_bestanden = st.slider("Max bronnen ophalen:", min_value=5, max_value=50, value=15, step=5)
    submit_knop = st.form_submit_button("Voer Onderzoek Uit", type="primary")

# HOOFDLOGICA BIJ ZOEKEN
if submit_knop and onderzoeksvraag.strip():
    # Sessie resetten voor nieuwe zoekopdracht
    st.session_state.actieve_chat = None
    st.session_state.messages = []

    st.subheader("🔎 Stap 1: Inhoudsopgave Scannen")
    
    try:
        sh = gc.open(SHEET_NAAM)
        worksheet = sh.sheet1
        alle_records = worksheet.get_all_records()
        data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
    except Exception as e:
        st.error(f"Kon de Google Sheet '{SHEET_NAAM}' niet openen: {e}")
        st.stop()

    if not data:
        st.error("De Google Sheet is leeg of bevat geen geldige gegevens.")
        st.stop()

    # Directe match
    geselecteerde_bestanden = []
    for row in data:
        b_naam_sheet = str(row.get('Bestandsnaam', '')).strip()
        if b_naam_sheet and b_naam_sheet.lower() in onderzoeksvraag.lower():
            geselecteerde_bestanden.append(b_naam_sheet)

    # AI Filter op index
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
        Jij bent hoofdarchivaris. Hieronder staat de inhoudsopgave van ons archief:

        {index_tekst}

        ONDERZOEKSVRAAG: "{onderzoeksvraag}"

        CRUCIALE INSTRUCTIES:
        1. Welke bestanden/foto's uit de index zijn het meest relevant voor deze specifieke vraag?
        2. Let heel goed op de FIRMANAAM, PERSONEN, DATUM/JAARTAL en ONDERWERPEN.
        3. BELANGRIJK BIJ MULTI-PAGINA DOCUMENTEN EN STAATSBLADEN:
           Als een relevant document of staatsblad uit meerdere bladzijden bestaat (bijv. blz. 1, blz. 2, pag 1, pag 2) of als een melding/akte doorloopt, selecteer dan VERPLICHT OOK de direct opvolgende pagina('s) van datzelfde document of jaartal, zelfs als de bedrijfsnaam op de vervolgpagina niet expliciet herhaald wordt!
        4. Geef maximaal {max_bestanden} meest relevante bestandsnamen terug.

        Geef UITSLUITEND de bestandsnamen terug gescheiden door komma's. Geen extra tekst.
        """

        with st.spinner("AI scant de inhoudsopgave op relevante documenten en opvolgende pagina's..."):
            try:
                res_filter = stuur_met_retry(ai_client, filter_prompt, is_chat=False)
                geselecteerde_bestanden = [b.strip() for b in res_filter.text.split(',') if b.strip()]
            except Exception as e:
                st.error(f"Fout bij filteren van de index: {e}")
                st.stop()

    st.write(f"**Geselecteerde documenten ({len(geselecteerde_bestanden)} stuks):**")
    for b in geselecteerde_bestanden:
        st.markdown(f"- `{b}`")

    if not geselecteerde_bestanden:
        st.warning("Geen relevante bestanden gevonden op basis van je zoekopdracht.")
        st.stop()

    # OPHALEN UIT DRIVE
    st.subheader("📥 Stap 2: Originele Documenten Ophalen & Analyseren")
    
    onderzoeks_payload = [
        f"""Jij bent een financieel-historisch expert en hoofdarchivaris.
Beantwoord onderstaande onderzoeksvraag grondig en nauwkeurig op basis van de meegeleverde originele archiefstukken en/of foto's.

ONDERZOEKSVRAAG: {onderzoeksvraag}

CRUCIALE INSTRUCTIES VOOR STRUCTUUR EN CONCLUSIE:
1. RESPECTEER STRIKT DE CHRONOLOGIE EN WIJZIGINGEN:
   - Voeg NIET zomaar namen uit verschillende documenten of jaartallen samen tot één enkele statische lijst.
   - Als document A (bijv. begin 1936) andere bestuursleden/voorzitters noemt dan document B (bijv. eind 1936 of 1937), meld dan expliciet dat er een BESTUURSWIJZIGING, OPVOLGING of WISSEL heeft plaatsgevonden.

2. IN HET EINDRAPPORT EN DE CONCLUSIE:
   - Presenteer een CHRONOLOGISCH OVERZICHT in plaats van een gemengde/samengevoegde lijst.
   - Bv: "Oorspronkelijk bestuur volgens bron A (datum X): Voorzitter A..." gevolgd door "Bestuurswijziging volgens bron B (datum Y): Nieuwe voorzitter B t.o.v. A...".
   - Zet NOOIT twee verschillende personen op precies dezelfde functie (zoals voorzitter) in hetzelfde overzicht zonder uit te leggen wie wie opvolgde en volgens welk document.

3. EXPLICITEER PER ARCHIEFSTUK EN GEBRUIK EXACTE CITATEN:
   - Controleer ALLE documenten en pagina's van boven naar beneden.
   - Wijs ALLEEN bestuursleden of functies toe aan een firma waaronder ze daadwerkelijk vermeld staan.
   - Vermeld per gevonden persoon de exacte functie en de geciteerde bestandsnaam (bijv. 'staatsblad1936-03-28blz2548.jpg').
"""
    ]

    geladen_aantal = 0
    cols = st.columns(3)

    for i, b_naam in enumerate(geselecteerde_bestanden):
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
            drive_view_url = f"https://drive.google.com/file/d/{b_id}/view"

            try:
                # 1. GOOGLE DOCS
                if b_mime == 'application/vnd.google-apps.document':
                    req = drive_service.files().export_media(fileId=b_id, mimeType='text/plain')
                    doc_txt = req.execute().decode('utf-8', errors='ignore')
                    onderzoeks_payload.append(f"\n--- INHOUD GOOGLE DOC ({b_real_naam}) ---\n{doc_txt}")
                    st.markdown(f"📄 **{b_real_naam}** ([Open Google Doc]({drive_view_url}))")

                # 2. PDF BESTANDEN
                elif b_mime == 'application/pdf' or b_real_naam.lower().endswith('.pdf'):
                    req = drive_service.files().get_media(fileId=b_id)
                    pdf_bytes = req.execute()

                    pdf_part = types.Part.from_bytes(
                        data=pdf_bytes,
                        mime_type='application/pdf'
                    )
                    onderzoeks_payload.append(f"\n--- ORIGINELE PDF: {b_real_naam} ---")
                    onderzoeks_payload.append(pdf_part)
                    st.markdown(f"📕 **{b_real_naam}** ([Open PDF in Drive]({drive_view_url}))")

                # 3. AFBEELDINGEN
                else:
                    req = drive_service.files().get_media(fileId=b_id)
                    f_data = req.execute()

                    img = Image.open(io.BytesIO(f_data))
                    if img.mode != 'RGB':
                        img = img.convert('RGB')

                    col_idx = geladen_aantal % 3
                    with cols[col_idx]:
                        st.image(img, caption=b_real_naam, use_container_width=True)
                        st.markdown(f"[🔍 Open Hoge Resolutie]({drive_view_url})")

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

    # ANALYSE UITVOEREN
    if geladen_aantal > 0:
        with st.spinner("🔍 Gemini voert een diepgaande analyse uit op alle documenten..."):
            chat = ai_client.chats.create(model=MODEL_NAAM)
            response = stuur_met_retry(chat, onderzoeks_payload, is_chat=True)

            st.session_state.actieve_chat = chat
            st.session_state.messages = [{"role": "assistant", "content": response.text}]

# CHAT INTERFACE VOOR VERVOLGVRAAGEN
if "actieve_chat" in st.session_state and st.session_state.actieve_chat:
    st.markdown("---")
    st.subheader("📊 Onderzoeksrapport")

    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    vervolgvraag = st.chat_input("Stel een vervolgvraag over deze resultaten...")
    if vervolgvraag:
        st.session_state.messages.append({"role": "user", "content": vervolgvraag})
        with st.chat_message("user"):
            st.markdown(vervolgvraag)

        with st.chat_message("assistant"):
            with st.spinner("Analyseren..."):
                res = stuur_met_retry(st.session_state.actieve_chat, vervolgvraag, is_chat=True)
                st.markdown(res.text)
                st.session_state.messages.append({"role": "assistant", "content": res.text})
