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

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: Wat weet je over de radio model Vedette?',
        height=100
    )
with col2:
    max_dossiers = st.slider("Max dossiers / bestanden:", min_value=5, max_value=50, value=15, step=5)

btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

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

            geselecteerde_keys = []  # Dit kan een Document_ID (bijv DOC_0205) óf een Bestandsnaam zijn

            # A. HARD-CHECK op specifieke zoekwoorden (zoals 'vedette')
            zoek_woorden = [w.lower() for w in onderzoeksvraag.split() if len(w) > 3]
            negeer_woorden = ['radio', 'model', 'over', 'weet', 'geef', 'welke', 'document', 'dossier']

            for row in data:
                doc_id = str(row.get('Document_ID', '')).strip()
                b_naam = str(row.get('Bestandsnaam', '')).strip()
                rij_tekst = " ".join([str(v) for v in row.values()]).lower()
                
                # Bepaal sleutel (gebruik Document_ID als die bestaat, anders Bestandsnaam)
                key_id = doc_id if doc_id else b_naam

                for w in zoek_woorden:
                    if w in rij_tekst and w not in negeer_woorden:
                        if key_id and key_id not in geselecteerde_keys:
                            geselecteerde_keys.append(key_id)
                        break

            # B. GEMINI INDEX SCANNING
            # Groepeer gegevens per Document_ID of los bestand
            dossier_samenvattingen = {}
            for row in data:
                doc_id = str(row.get('Document_ID', '')).strip()
                b_naam = str(row.get('Bestandsnaam', '')).strip()
                key_id = doc_id if doc_id else b_naam

                if key_id not in dossier_samenvattingen:
                    dossier_samenvattingen[key_id] = {
                        "Bestanden": set(),
                        "Datum": row.get('Datum Document', 'Onbekend'),
                        "Personen": set(),
                        "Onderwerpen": set(),
                    }
                
                dossier_samenvattingen[key_id]["Bestanden"].add(b_naam)
                if row.get('Genoemde Personen'):
                    dossier_samenvattingen[key_id]["Personen"].add(str(row.get('Genoemde Personen')))
                if row.get('Onderwerp (NL)'):
                    dossier_samenvattingen[key_id]["Onderwerpen"].add(str(row.get('Onderwerp (NL)')))

            index_regels = []
            for d_id, d_info in dossier_samenvattingen.items():
                pers_str = ", ".join(d_info["Personen"]) if d_info["Personen"] else "Geen"
                ond_str = ", ".join(d_info["Onderwerpen"]) if d_info["Onderwerpen"] else "Geen"
                best_str = ", ".join(list(d_info["Bestanden"]))
                
                regel = f"ID/Dossier: {d_id} | Bestanden in dossier: {best_str} | Onderwerp: {ond_str} | Personen: {pers_str}"
                index_regels.append(regel)

            index_tekst = "\n".join(index_regels)
            if len(index_tekst) > 250000:
                index_tekst = index_tekst[:250000]

            filter_prompt = f"""
Jij bent hoofdarchivaris. Hieronder staat de index van alle dossiers (zoals DOC_0205) en losse bestanden (PDF/JPG) in ons archief:

{index_tekst}

ONDERZOEKSVRAAG: "{onderzoeksvraag}"

INSTRUCTIES VOOR SELECTIE:
1. Selecteer de ID's / Dossiernamen / Bestandsnamen die direct inhoudelijk aansluiten op de vraag.
2. Selecteer expliciet (PDF-)bestanden of documenten waarin de specifieke type- of modelnaam uit de vraag voorkomt.
3. Sluit algemene radiodistributiedossiers, schadeclaims of bestuurderslijsten (zoals Kempische Radio Centrales) UIT, behalve als er specifiek naar gevraagd wordt.
4. Geef maximaal {max_dossiers} ID's terug.

Geef UITSLUITEND de exacte ID's/Dossiers/Bestandsnamen terug gescheiden door komma's. Geen extra tekst of uitleg.
"""

            try:
                res_filter = genereer_met_retry(ai_client, MODEL_NAAM, filter_prompt)
                ai_ids = [d.strip() for d in res_filter.text.split(',') if d.strip()]
                
                for ai_id in ai_ids:
                    if ai_id not in geselecteerde_keys:
                        geselecteerde_keys.append(ai_id)
            except Exception as e:
                st.error(f"Fout tijdens het scannen van de index ({MODEL_NAAM}): {e}")

        if not geselecteerde_keys:
            st.warning("Geen relevante documenten gevonden op basis van de zoekopdracht.")
            st.stop()

        # BEPERK TOT HET INGESTELDE MAXIMUM EN VERZAMEL ALLE GERELATEERDE BESTANDEN
        geselecteerde_keys = geselecteerde_keys[:max_dossiers]
        
        eind_bestanden_lijst = []
        for row in data:
            doc_id = str(row.get('Document_ID', '')).strip()
            b_naam = str(row.get('Bestandsnaam', '')).strip()
            
            # Check of het bestand hoort bij een geselecteerd Document_ID óf direct matchet met een bestandsnaam
            match = False
            for key in geselecteerde_keys:
                if (doc_id and doc_id.lower() == key.lower()) or \
                   (b_naam and b_naam.lower() == key.lower()) or \
                   (key.lower().startswith('single_') and key.lower().replace('single_', '') == b_naam.lower()):
                    match = True
                    break
            
            if match and b_naam and b_naam not in eind_bestanden_lijst:
                eind_bestanden_lijst.append(b_naam)

        # STAP 2: Originele documenten ophalen uit Google Drive
        with st.spinner(f"Stap 2/3: Originele bestanden ophalen uit Drive ({len(eind_bestanden_lijst)} pagina's/bestanden verzameld)..."):
            onderzoeks_payload = [
                f"""Jij bent een financieel-historisch expert en archivaris.
Beantwoord onderstaande onderzoeksvraag grondig en gedetailleerd op basis van de meegeleverde originele archiefstukken (afbeeldingen EN PDF's).

ONDERZOEKSVRAAG: {onderzoeksvraag}

INSTRUCTIES VOOR JE RAPPORT:
1. Beantwoord de vraag op basis van de geleverde bestanden.
2. Vermeld alle concrete namen, specificaties, type-nummers, jaartallen en details die je vindt.
3. Citeer steeds de exacte bestandsnaam (bijv. 'document.pdf' of 'foto.jpg') als bron.
4. Trek een duidelijke conclusie.
"""
            ]

            geladen_aantal = 0
            for b_naam in eind_bestanden_lijst:
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

# ------------------------------------------------------------------------------
# 5. WEERGAVE BRONNEN MET PREVIEWS & RAPPORT
# ------------------------------------------------------------------------------
if st.session_state.bron_details:
    st.subheader("📁 Geselecteerde bronnen & Documenten:")
    
    cols = st.columns(3)
    for index, bron in enumerate(st.session_state.bron_details):
        b_naam = bron["naam"]
        b_id = bron["id"]
        b_mime = bron.get("mime", "")
        
        thumbnail_url = f"https://drive.google.com/thumbnail?id={b_id}&sz=w800"
        drive_view_url = f"https://drive.google.com/file/d/{b_id}/view"

        with cols[index % 3]:
            with st.expander(f"📄 {b_naam}", expanded=True):
                if b_mime == 'application/pdf' or b_naam.lower().endswith('.pdf'):
                    st.info("📕 **PDF Document**")
                    try:
                        st.image(thumbnail_url, caption=b_naam, use_container_width=True)
                    except Exception:
                        st.write("*(Geen voorbeeldweergave beschikbaar voor deze PDF)*")
                else:
                    st.image(thumbnail_url, caption=b_naam, use_container_width=True)
                
                st.link_button("🔍 Open origineel in Google Drive", drive_view_url)

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
