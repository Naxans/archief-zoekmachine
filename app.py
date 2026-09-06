import io
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

# ==============================================================================
# ARCHIEF ZOEKMACHINE - VERSIE INFORMATIE
# ==============================================================================
# Versie: v1.0.1
# Datum: September 2026
#
# CHRONOLOGISCHE VERSIE-HISTORIE:
# - v3.8.1: Oorspronkelijke vertrouwde schermlayout en presentatie van rapport, bronnen en downloads.
# - v1.0.0: Introductie van Query Expansion (AI-tussenstation) voor automatische verrijking 
#           van zoekvragen (Franse/Nederlandse naamvarianten, synoniemen, chronologie).
# - v1.0.1: Herstel van de exacte v3.8.1 schermlayout, verpakt in een schone v1.0.x versiestructuur.
# ==============================================================================

APP_VERSIE = "v1.0.1"

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
# 2. CONFIGURATIE & DYNAMISCHE MODEL-DETECTIE
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

def bepaal_werkend_model(client):
    """Test aliassen die ondersteund worden door jouw API-sleutel."""
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
    """Voert een API-call uit en vangt 429/503 fouten op met retry logic."""
    for poging in range(max_retries):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            err_msg = str(e)
            if "503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if poging < max_retries - 1:
                    wachttijd = 15 * (poging + 1)
                    st.info(f"⏳ Gemini servers zijn druk of limiet bereikt ({'503 Overbelast' if '503' in err_msg else '429 Limiet'}). Pauze van {wachttijd}s voor poging {poging + 2}/{max_retries}...")
                    time.sleep(wachttijd)
                    continue
                else:
                    st.error("⚠️ De limiet voor de Gemini API is tijdelijk bereikt of de servers zijn te druk. Wacht 1-2 minuten en probeer opnieuw.")
            raise e

# ------------------------------------------------------------------------------
# GEMINI ALS TUSSENSTATION (QUERY EXPANSION)
# ------------------------------------------------------------------------------
def verrijk_zoekopdracht_met_gemini(client, model, originele_vraag):
    """
    Gebruikt Gemini als taalkundig en historisch tussenstation (Query Expansion).
    Genereert synoniemen, Franse/Nederlandse naamvarianten en houdt
    rekening met publicatiedata in Belgische archieven (1900-1960).
    """
    prompt = f"""
Jij bent een taalkundig en historisch expert gespecialiseerd in Belgische bedrijfs- en archiefstukken (periode 1900-1960).
De gebruiker stelt de volgende zoekvraag in ons archief: "{originele_vraag}"

ANALYSEER EN VERRIJK DEZE ZOEKVRAAG:
1. Vertaal namen naar hun Franse en Nederlandse varianten (bijv. Emiel <-> Emile, Charles <-> Karel, Jean <-> Jan, Jules <-> Julien).
2. Voeg mogelijke initialen of schrijfvarianten toe (bijv. E. Delvoie, Delvoie Emile).
3. Voeg relevante vaktermen, bedrijfsvormen of synoniemen toe in het Frans en Nederlands (bijv. Radio <-> T.S.F. / Télégraphie sans fil, Staatsblad <-> Moniteur Belge, Bestuurder <-> Administrateur).
4. Houd rekening met de chronologie van archieven: een vraag over boekjaar X (bijv. 1936) kan leiden tot publicaties in jaar X+1 (bijv. 1937 in het Staatsblad). Voeg eventueel het volgend jaar toe als relevant trefwoord.

Geef UITSLUITEND een compacte, door komma's gescheiden lijst van trefwoorden en naamvarianten terug. Geen toelichting of extra tekst.
"""
    try:
        res = genereer_met_retry(client, model, prompt)
        return res.text.strip()
    except Exception:
        return originele_vraag

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
# 3. STREAMLIT INTERFACE (LAYOUT v3.8.1 STIJL)
# ------------------------------------------------------------------------------
st.set_page_config(page_title=f"Archief Zoekmachine {APP_VERSIE}", page_icon="🔍", layout="wide")
st.title(f"🔍 Archief Zoekmachine ({APP_VERSIE})")

if MODEL_NAAM:
    st.caption(f"Versie: `{APP_VERSIE}` | Actief AI-model: `{MODEL_NAAM}` | Vertrouwde v3.8.1 schermlayout met Query Expansion")
else:
    st.error("Kon geen werkend Gemini-model vinden. Controleer je Gemini API key.")
    st.stop()

# Invoer van de onderzoeksvraag & parameters
col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: wie waren de bestuursleden van de firma radio belge de construction in 1936?',
        height=100
    )
with col2:
    max_dossiers = st.slider("Max dossiers (Document_ID's):", min_value=5, max_value=50, value=15, step=5)

# Knoppenbalk
btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

if stop_button:
    st.session_state.gestopt = True
    st.warning("⚠️ Onderzoek is geannuleerd.")
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

        # TUSSENSTATION: Zoekopdracht verrijken via Gemini (Query Expansion)
        with st.spinner("🧠 Tussenstation: Gemini analyseert taalkundige en historische varianten..."):
            verrijkte_termen = verrijk_zoekopdracht_met_gemini(ai_client, MODEL_NAAM, onderzoeksvraag)
            
        with st.expander("🧠 Bekijk de door Gemini verrijkte zoektermen (Query Expansion)", expanded=False):
            st.write(f"**Originele vraag:** {onderzoeksvraag}")
            st.write(f"**Verrijkte trefwoorden & varianten:** {verrijkte_termen}")

        # STAP 1: Inhoudsopgave scannen uit Google Sheet
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

            if st.session_state.gestopt:
                st.stop()

            geselecteerde_doc_ids = []

            # Directe match check
            combinatie_zoektekst = f"{onderzoeksvraag} {verrijkte_termen}".lower()
            for row in data:
                doc_id_val = str(row.get('Document_ID', '')).strip()
                b_naam_val = str(row.get('Bestandsnaam', '')).strip()

                if (doc_id_val and doc_id_val.lower() in combinatie_zoektekst) or \
                   (b_naam_val and b_naam_val.lower() in combinatie_zoektekst):
                    if doc_id_val and doc_id_val not in geselecteerde_doc_ids:
                        geselecteerde_doc_ids.append(doc_id_val)

            # Filter via Gemini op samenvattingen
            if not geselecteerde_doc_ids:
                dossier_samenvattingen = {}
                for row in data:
                    doc_id = str(row.get('Document_ID', '')).strip()
                    if not doc_id:
                        doc_id = f"SINGLE_{row.get('Bestandsnaam', '').strip()}"

                    if doc_id not in dossier_samenvattingen:
                        dossier_samenvattingen[doc_id] = {
                            "Datum": row.get('Datum Document', 'Onbekend'),
                            "Personen": set(),
                            "Onderwerpen": set(),
                            "Inhoud": set()
                        }

                    if row.get('Genoemde Personen'):
                        dossier_samenvattingen[doc_id]["Personen"].add(str(row.get('Genoemde Personen')).strip())
                    if row.get('Onderwerp (NL)'):
                        dossier_samenvattingen[doc_id]["Onderwerpen"].add(str(row.get('Onderwerp (NL)')).strip())

                    inhoud_val = (
                        row.get('Inhoud & Cijfers (NL)') or 
                        row.get('Inhoud & cijfers (NL)') or 
                        row.get('Inhoud & Cijfers') or 
                        row.get('Inhoud & cijfers') or 
                        row.get('Inhoud') or 
                        ''
                    )
                    if inhoud_val:
                        dossier_samenvattingen[doc_id]["Inhoud"].add(str(inhoud_val).strip())

                index_regels = []
                for d_id, d_info in dossier_samenvattingen.items():
                    pers_str = ", ".join(d_info["Personen"]) if d_info["Personen"] else "Geen"
                    ond_str = ", ".join(d_info["Onderwerpen"]) if d_info["Onderwerpen"] else "Geen"
                    inhoud_str = " | ".join(d_info["Inhoud"]) if d_info["Inhoud"] else "Geen"

                    regel = f"Document_ID: {d_id} | Datum: {d_info['Datum']} | Personen: {pers_str} | Onderwerp: {ond_str} | Inhoud: {inhoud_str}"
                    index_regels.append(regel)

                index_tekst = "\n".join(index_regels)
                if len(index_tekst) > 250000:
                    index_tekst = index_tekst[:250000]

                filter_prompt = f"""
Jij bent een zeer strenge en nauwkeurige hoofdarchivaris. Hieronder staat het overzicht van unieke dossiers in ons archief:

{index_tekst}

ORIGINELE VRAAG: "{onderzoeksvraag}"
VERRIJKTE ZOEKTERMEN EN NAAMVARIANTEN: "{verrijkte_termen}"

CRITISCHE SELECTIECRITERIA:
1. Selecteer dossiers (Document_ID's) die inhoudelijk of op naam/onderwerp overeenkomen met de vraag of de verrijkte zoektermen.
2. Geef maximaal {max_dossiers} relevante Document_ID's terug.
3. ALLES OF NIETS: Als er absoluut GEEN enkel dossier relevant is, antwoord dan UITSLUITEND met het woord: GEEN_MATCH.

Geef UITSLUITEND de exacte Document_ID's terug gescheiden door komma's, OF het woord GEEN_MATCH. Geen extra uitleg.
"""

                try:
                    res_filter = genereer_met_retry(ai_client, MODEL_NAAM, filter_prompt)
                    raw_text = res_filter.text.strip()

                    negatieve_termen = ["geen_match", "geen resultaten", "geen documenten", "niets gevonden"]
                    if any(term in raw_text.lower() for term in negatieve_termen):
                        geselecteerde_doc_ids = []
                    else:
                        geselecteerde_doc_ids = [d.strip() for d in raw_text.split(',') if d.strip()]
                except Exception as e:
                    st.error(f"Fout tijdens het scannen van de index: {e}")
                    st.stop()

        if not geselecteerde_doc_ids:
            st.warning("⚠️ Geen relevante documenten gevonden in het archief voor deze zoekopdracht.")
            st.stop()

        # STAP 1.5: Verzamel alle gekoppelde bestanden
        eind_bestanden_lijst = []
        for row in data:
            doc_id = str(row.get('Document_ID', '')).strip()
            b_naam = str(row.get('Bestandsnaam', '')).strip()

            if any(doc_id.lower() == g_id.lower() or b_naam.lower() == g_id.lower() for g_id in geselecteerde_doc_ids):
                if b_naam and b_naam not in eind_bestanden_lijst:
                    eind_bestanden_lijst.append(b_naam)

        with st.expander("🔍 Details van de geselecteerde documenten uit de Sheet", expanded=True):
            st.write(f"**Geselecteerde Document_ID's:** `{geselecteerde_doc_ids}`")
            st.write(f"**Gekoppelde bestandsnamen op Drive:** `{eind_bestanden_lijst}`")

        if not eind_bestanden_lijst:
            st.error("Er staan geen geldige bestandsnamen gekoppeld aan de geselecteerde ID's in de Google Sheet.")
            st.stop()

        # STAP 2: Originele bestanden ophalen uit Google Drive
        with st.spinner(f"Stap 2/3: Originele bestanden ophalen uit Drive ({len(eind_bestanden_lijst)} bestanden)..."):
            onderzoeks_payload = [
                f"""Jij bent een financieel-historisch expert en archivaris.
Beantwoord onderstaande onderzoeksvraag grondig en gedetailleerd op basis van de meegeleverde originele archiefstukken.

ONDERZOEKSVRAAG: {onderzoeksvraag}
VERRIJKTE ZOEKCONTEXT: {verrijkte_termen}

INSTRUCTIES VOOR JE RAPPORT:
1. Richt je specifiek op de gevraagde firma, personen, modellen en periode.
2. Structureer je antwoord helder.
3. Vermeld alle concrete namen, functies, cijfers en details die op de documenten staan.
4. Citeer steeds de bestandsnaam wanneer je naar specifieke informatie verwijst.
5. Trek een heldere conclusie als antwoord op de vraag.
"""
            ]

            geladen_aantal = 0
            missing_files = []

            for b_naam in eind_bestanden_lijst:
                if st.session_state.gestopt:
                    st.stop()

                b_naam_schoon = str(b_naam).strip("'\" ")
                if ":" in b_naam_schoon:
                    b_naam_schoon = b_naam_schoon.split(":", 1)[-1].strip()

                basis_naam = b_naam_schoon.split('/')[-1]
                naam_zonder_ext = basis_naam.rsplit('.', 1)[0] if '.' in basis_naam else basis_naam

                bestanden = []
                query1 = f"name = '{b_naam_schoon}' and trashed = false"
                res1 = drive_service.files().list(q=query1, fields='files(id, name, mimeType)').execute()
                bestanden = res1.get('files', [])

                if not bestanden and basis_naam != b_naam_schoon:
                    query2 = f"name = '{basis_naam}' and trashed = false"
                    res2 = drive_service.files().list(q=query2, fields='files(id, name, mimeType)').execute()
                    bestanden = res2.get('files', [])

                if not bestanden and len(naam_zonder_ext) > 1:
                    query3 = f"name contains '{naam_zonder_ext}' and trashed = false"
                    res3 = drive_service.files().list(q=query3, fields='files(id, name, mimeType)').execute()
                    bestanden = res3.get('files', [])

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

                            img_part = types.Part.from_bytes(data=img_byte_arr.getvalue(), mime_type='image/jpeg')
                            onderzoeks_payload.append(f"\n--- ORIGINELE AFBEELDING: {b_real_naam} ---")
                            onderzoeks_payload.append(img_part)

                        geladen_aantal += 1
                    except Exception as e:
                        st.warning(f"Kon {b_real_naam} niet laden: {e}")
                else:
                    missing_files.append(b_naam_schoon)

        if missing_files:
            st.warning(f"⚠️ Niet gevonden op Drive: {missing_files}")

        if geladen_aantal == 0:
            st.error("Geen van de geselecteerde bestanden kon worden geladen uit Google Drive.")
            st.stop()

        # STAP 3: Analyse uitvoeren via Gemini
        with st.spinner("Stap 3/3: Eindanalyse en rapport genereren via Gemini..."):
            if st.session_state.gestopt:
                st.stop()

            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
            except Exception as e:
                st.error(f"Fout tijdens Gemini analyse: {e}")

# ------------------------------------------------------------------------------
# 5. WEERGAVE BRONNEN & RAPPORT (v3.8.1 STIJL)
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
