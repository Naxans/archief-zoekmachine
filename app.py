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
# Versie: v1.0.8
# Datum: September 2026
#
# BUGFIXES & VERBETERINGEN:
# - Opslaan van verrijkte trefwoorden & onderzoeks_payload in session_state.
# - Voorkomt verdwijnen van de trefwoorden-expander en de NameError.
# - Ondersteuning voor bladeren/navigatie in Drive viewer.
# ==============================================================================

APP_VERSIE = "v1.0.8 (2026)"

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

def verrijk_zoekopdracht_met_gemini(client, model, originele_vraag):
    prompt = f"""
Jij bent een taalkundig en historisch expert gespecialiseerd in Belgische bedrijfs- en archiefstukken (periode 1900-1960).
De gebruiker stelt de volgende zoekvraag in ons archief: "{originele_vraag}"

ANALYSEER EN VERRIJK DEZE ZOEKVRAAG:
1. Vertaal namen naar hun Franse en Nederlandse varianten (bijv. Emiel <-> Emile, Charles <-> Karel, Jean <-> Jan, Jules <-> Julien).
2. Voeg mogelijke initialen of schrijfvarianten toe (bijv. E. Delvoie, Delvoie Emile).
3. Voeg relevante vaktermen, bedrijfsvormen of overlijdenssynoniemen toe in het Frans en Nederlands (bijv. overlijden, décès, mort, décédé, overleden, sterfte, necrologie, nécrologie, burgerlijke stand, état civil, familiebericht, faire-part, testament, erfenis, succession).

Geef UITSLUITEND een compacte, door komma's gescheiden lijst van trefwoorden en naamvarianten terug. Geen toelichting.
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
if "gestopt" not in st.session_state:
    st.session_state.gestopt = False
if "verrijkte_termen" not in st.session_state:
    st.session_state.verrijkte_termen = ""
if "onderzoeks_payload" not in st.session_state:
    st.session_state.onderzoeks_payload = []
if "laatste_vraag" not in st.session_state:
    st.session_state.laatste_vraag = ""

# ------------------------------------------------------------------------------
# 3. INTERFACE (v1.0.8)
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
        margin-bottom: 15px;
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
</style>
""", unsafe_allow_html=True)

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
        placeholder='wie waren de bestuursleden van de firma radio belge de construction in 1936?',
        height=90
    )
with col2:
    max_dossiers = st.slider("Max dossiers (Document_ID's):", min_value=5, max_value=50, value=15, step=5)

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
        st.session_state.totaal_paginas = 0
        st.session_state.laatste_vraag = onderzoeksvraag

        with st.spinner("🧠 Tussenstation: Gemini analyseert taalkundige en historische varianten..."):
            st.session_state.verrijkte_termen = verrijk_zoekopdracht_met_gemini(ai_client, MODEL_NAAM, onderzoeksvraag)

        with st.spinner("Inhoudsopgave scannen..."):
            try:
                sh = gc.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Fout bij openen Google Sheet: {e}")
                st.stop()

            # Dossier naar Pagina-telling mapping
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

            alle_termen = [t.strip().lower() for t in f"{onderzoeksvraag}, {st.session_state.verrijkte_termen}".split(',') if len(t.strip()) > 1]
            
            geselecteerde_doc_ids = []
            for row in data:
                doc_id_val = str(row.get('Document_ID', '')).strip()
                b_naam_val = str(row.get('Bestandsnaam', '')).strip()
                rij_tekst = f"{doc_id_val} {b_naam_val} {row.get('Genoemde Personen', '')} {row.get('Onderwerp (NL)', '')} {row.get('Inhoud & Cijfers (NL)', '')}".lower()

                if any(t in rij_tekst for t in alle_termen):
                    target_id = doc_id_val if doc_id_val else b_naam_val
                    if target_id and target_id not in geselecteerde_doc_ids:
                        geselecteerde_doc_ids.append(target_id)

            geselecteerde_doc_ids = geselecteerde_doc_ids[:max_dossiers]

        if not geselecteerde_doc_ids:
            st.warning("⚠️ Geen relevante documenten gevonden.")
            st.stop()

        # Payload opbouwen & in session_state bewaren
        st.session_state.onderzoeks_payload = [
            f"ONDERZOEKSVRAAG: {onderzoeksvraag}\nVERRIJKTE CONTEXT: {st.session_state.verrijkte_termen}\nBeantwoord de vraag zo volledig mogelijk met bronvermelding per dossier."
        ]

        with st.spinner("Documenten ophalen uit Drive..."):
            for doc_id in geselecteerde_doc_ids:
                info = dossier_pagina_map.get(doc_id, {"bestanden": [doc_id], "aantal_paginas": 1})
                pag_count = info["aantal_paginas"]
                st.session_state.totaal_paginas += pag_count

                eerste_bestand = info["bestanden"][0]
                b_naam_schoon = str(eerste_bestand).strip("'\" ")
                if ":" in b_naam_schoon: b_naam_schoon = b_naam_schoon.split(":", 1)[-1].strip()
                basis_naam = b_naam_schoon.split('/')[-1]

                query = f"name contains '{basis_naam.rsplit('.', 1)[0]}' and trashed = false"
                res = drive_service.files().list(q=query, fields='files(id, name, parents, mimeType)').execute()
                bestanden = res.get('files', [])

                if bestanden:
                    f = bestanden[0]
                    b_id, b_real_naam = f['id'], f['name']
                    parent_id = f.get('parents', [None])[0]
                    
                    weergave_titel = f"{doc_id} ({pag_count} pag.)" if pag_count > 1 else doc_id
                    
                    # Als er meerdere pagina's in het dossier zitten, koppel naar de parent folder voor bladerfunctionaliteit
                    drive_url = f"https://drive.google.com/drive/folders/{parent_id}" if (pag_count > 1 and parent_id) else f"https://drive.google.com/file/d/{b_id}/view"

                    st.session_state.bron_details.append({
                        "naam": weergave_titel,
                        "id": b_id,
                        "url": drive_url
                    })

                    try:
                        req = drive_service.files().get_media(fileId=b_id)
                        f_data = req.execute()
                        img = Image.open(io.BytesIO(f_data))
                        if img.mode != 'RGB': img = img.convert('RGB')
                        img.thumbnail((800, 800))
                        img_byte_arr = io.BytesIO()
                        img.save(img_byte_arr, format='JPEG', quality=70)

                        img_part = types.Part.from_bytes(data=img_byte_arr.getvalue(), mime_type='image/jpeg')
                        st.session_state.onderzoeks_payload.append(f"\n--- DOSSIER: {doc_id} (Bestand: {b_real_naam}) ---")
                        st.session_state.onderzoeks_payload.append(img_part)
                    except Exception:
                        pass

        st.rerun()

# ------------------------------------------------------------------------------
# 5. WEERGAVE RESULTATEN
# ------------------------------------------------------------------------------
if st.session_state.verrijkte_termen:
    with st.expander("🧠 Bekijk de door Gemini verrijkte zoektermen (Query Expansion)", expanded=True):
        st.write(f"**Originele vraag:** {st.session_state.laatste_vraag}")
        st.write(f"**Verrijkte trefwoorden & varianten:** {st.session_state.verrijkte_termen}")

if st.session_state.bron_details:
    st.markdown("---")
    aantal_dossiers = len(st.session_state.bron_details)
    totaal_pag = st.session_state.totaal_paginas
    
    st.subheader(f"🖼️ Geselecteerde Archiefdocumenten ({aantal_dossiers} dossiers • {totaal_pag} pagina's)")
    st.caption("Klik op een tegel om het document/boek te openen in de viewer.")

    cols = st.columns(6)
    for idx, bron in enumerate(st.session_state.bron_details):
        b_naam = bron["naam"]
        b_id = bron["id"]
        drive_url = bron.get("url", f"https://drive.google.com/file/d/{b_id}/view")
        thumbnail_url = f"https://drive.google.com/thumbnail?id={b_id}&sz=w400"

        col = cols[idx % 6]
        with col:
            st.markdown(f"""
                <div class="doc-card">
                    <a href="{drive_url}" target="_blank">
                        <img src="{thumbnail_url}" alt="{b_naam}">
                    </a>
                    <div class="doc-title">{b_naam}</div>
                </div>
            """, unsafe_allow_html=True)

    if not st.session_state.chat_historie and not st.session_state.gestopt:
        with st.spinner("📑 Historisch Onderzoeksrapport genereren met Gemini..."):
            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, st.session_state.onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
                st.rerun()
            except Exception as e:
                st.error(f"Fout tijdens analyse: {e}")

if st.session_state.chat_historie:
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
