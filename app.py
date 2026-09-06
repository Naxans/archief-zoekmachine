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
# Versie: v1.0.5
# Datum: September 2026
#
# CHRONOLOGISCHE VERSIE-HISTORIE:
# - v3.8.1: Oorspronkelijke schermlayout met tegel-grid van 6 kolommen.
# - v1.0.0: Introductie van Query Expansion (AI-tussenstation).
# - v1.0.3: Her-introductie van de controle-expander voor verrijkte trefwoorden.
# - v1.0.4: Poging tot versoepeling van het zoekfilter.
# - v1.0.5: GELAAGDE FILTERSTRATEGIE:
#           Stap 1: Zoek documenten van de persoon (verplichte naam-match).
#           Stap 2: Prioriteer binnen die selectie de stukken met specifieke
#                   overlijdenstermen (bijv. décès, necrologie, testament).
# ==============================================================================

APP_VERSIE = "v1.0.5 (2026)"

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

# Session state
if "actieve_chat" not in st.session_state:
    st.session_state.actieve_chat = None
if "chat_historie" not in st.session_state:
    st.session_state.chat_historie = []
if "bron_details" not in st.session_state:
    st.session_state.bron_details = []
if "gestopt" not in st.session_state:
    st.session_state.gestopt = False

# ------------------------------------------------------------------------------
# 3. INTERFACE (v1.0.5)
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
# 4. ONDERZOEKSLOGICA (GELAAGDE FILTERING - v1.0.5)
# ------------------------------------------------------------------------------
if submit_button:
    if not onderzoeksvraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
    else:
        st.session_state.gestopt = False
        st.session_state.chat_historie = []
        st.session_state.bron_details = []

        with st.spinner("🧠 Tussenstation: Gemini analyseert taalkundige en historische varianten..."):
            verrijkte_termen = verrijk_zoekopdracht_met_gemini(ai_client, MODEL_NAAM, onderzoeksvraag)
            
        with st.expander("🧠 Bekijk de door Gemini verrijkte zoektermen (Query Expansion)", expanded=True):
            st.write(f"**Originele vraag:** {onderzoeksvraag}")
            st.write(f"**Verrijkte trefwoorden & varianten:** {verrijkte_termen}")

        with st.spinner("Inhoudsopgave scannen..."):
            try:
                sh = gc.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Fout bij openen Google Sheet: {e}")
                st.stop()

            # Bepalen welke zoektermen namen zijn vs onderwerpen
            alle_termen = [t.strip().lower() for t in f"{onderzoeksvraag}, {verrijkte_termen}".split(',') if len(t.strip()) > 1]
            
            # Trefwoorden indelen op categorie
            overlijden_keywords = ["overlijden", "décès", "mort", "décédé", "overleden", "sterfte", "décès de", 
                                  "necrologie", "nécrologie", "burgerlijke stand", "état civil", "familiebericht", 
                                  "faire-part", "testament", "erfenis", "succession", "archivalia"]
            
            naam_termen = [t for t in alle_termen if not any(k in t for k in overlijden_keywords)]
            onderwerp_termen = [t for t in alle_termen if any(k in t for k in overlijden_keywords)]

            # STAP 1: Filteren op documenten die MINSTENS één naamvariant bevatten
            naam_matches = []
            for row in data:
                doc_id_val = str(row.get('Document_ID', '')).strip()
                b_naam_val = str(row.get('Bestandsnaam', '')).strip()
                rij_tekst = f"{doc_id_val} {b_naam_val} {row.get('Genoemde Personen', '')} {row.get('Onderwerp (NL)', '')} {row.get('Inhoud & Cijfers (NL)', '')}".lower()

                if any(naam in rij_tekst for naam in naam_termen if len(naam) > 2):
                    target_id = doc_id_val if doc_id_val else b_naam_val
                    if target_id and target_id not in [m['id'] for m in naam_matches]:
                        naam_matches.append({"id": target_id, "tekst": rij_tekst})

            # STAP 2: Binnen de naam-matches prioriteren op overlijdenstermen
            hoge_prioriteit_ids = []
            lage_prioriteit_ids = []

            for item in naam_matches:
                if any(kw in item["tekst"] for kw in onderwerp_termen):
                    hoge_prioriteit_ids.append(item["id"])
                else:
                    lage_prioriteit_ids.append(item["id"])

            # Combineer resultaten: eerst de specifieke overlijdensdocumenten, daarna eventuele overige documenten van de persoon
            geselecteerde_doc_ids = hoge_prioriteit_ids + lage_prioriteit_ids

            # STAP 3: AI-Fallback als er nog niks is gevonden
            if not geselecteerde_doc_ids:
                dossier_samenvattingen = {}
                for row in data:
                    doc_id = str(row.get('Document_ID', '')).strip() or f"SINGLE_{row.get('Bestandsnaam', '').strip()}"
                    if doc_id not in dossier_samenvattingen:
                        dossier_samenvattingen[doc_id] = {"Datum": row.get('Datum Document', 'Onbekend'), "Personen": set(), "Onderwerpen": set(), "Inhoud": set()}

                    if row.get('Genoemde Personen'): dossier_samenvattingen[doc_id]["Personen"].add(str(row.get('Genoemde Personen')).strip())
                    if row.get('Onderwerp (NL)'): dossier_samenvattingen[doc_id]["Onderwerpen"].add(str(row.get('Onderwerp (NL)')).strip())
                    inhoud_val = row.get('Inhoud & Cijfers (NL)') or row.get('Inhoud & cijfers (NL)') or row.get('Inhoud') or ''
                    if inhoud_val: dossier_samenvattingen[doc_id]["Inhoud"].add(str(inhoud_val).strip())

                index_regels = [
                    f"Document_ID: {d_id} | Datum: {d_info['Datum']} | Personen: {', '.join(d_info['Personen'])} | Onderwerp: {', '.join(d_info['Onderwerpen'])} | Inhoud: {' | '.join(d_info['Inhoud'])}"
                    for d_id, d_info in dossier_samenvattingen.items()
                ]
                index_tekst = "\n".join(index_regels)[:250000]

                filter_prompt = f"""
Jij bent een hoofdarchivaris. Hier is de index:
{index_tekst}

VRAAG VAN GEBRUIKER: "{onderzoeksvraag}"
ZOEKTERMEN: "{verrijkte_termen}"

OPDRACHT:
Selecteer maximaal {max_dossiers} relevante Document_ID's. Geef prioriteit aan documenten over overlijden, necrologie of stamboom van de gezochte persoon.
Als niks relevant is, antwoord GEEN_MATCH.
Geef enkel de komma-gescheiden lijst van ID's terug.
"""
                try:
                    res_filter = genereer_met_retry(ai_client, MODEL_NAAM, filter_prompt)
                    raw_text = res_filter.text.strip()
                    if "geen_match" not in raw_text.lower():
                        geselecteerde_doc_ids = [d.strip() for d in raw_text.split(',') if d.strip()]
                except Exception as e:
                    st.error(f"Fout tijdens scannen index: {e}")
                    st.stop()

        if not geselecteerde_doc_ids:
            st.warning("⚠️ Geen relevante documenten gevonden.")
            st.stop()

        # Beperk het aantal verwerkte dossiers tot de gekozen slider-waarde
        geselecteerde_doc_ids = geselecteerde_doc_ids[:max_dossiers]

        eind_bestanden_lijst = []
        for row in data:
            doc_id = str(row.get('Document_ID', '')).strip()
            b_naam = str(row.get('Bestandsnaam', '')).strip()
            if any(doc_id.lower() == g_id.lower() or b_naam.lower() == g_id.lower() for g_id in geselecteerde_doc_ids):
                if b_naam and b_naam not in eind_bestanden_lijst:
                    eind_bestanden_lijst.append(b_naam)

        # Drive ophalen
        with st.spinner(f"Documenten laden uit Drive ({len(eind_bestanden_lijst)} bestanden)..."):
            onderzoeks_payload = [f"ONDERZOEKSVRAAG: {onderzoeksvraag}\nVERRIJKTE CONTEXT: {verrijkte_termen}\nBeantwoord de vraag grondig met bronvermelding. Als er meerdere personen met dezelfde naam voorkomen (bijv. een priester en een ingenieur), vermeld dan beide overlijdensdatums als deze in de documenten te vinden zijn."]

            for b_naam in eind_bestanden_lijst:
                b_naam_schoon = str(b_naam).strip("'\" ")
                if ":" in b_naam_schoon: b_naam_schoon = b_naam_schoon.split(":", 1)[-1].strip()
                basis_naam = b_naam_schoon.split('/')[-1]

                query = f"name contains '{basis_naam.rsplit('.', 1)[0]}' and trashed = false"
                res = drive_service.files().list(q=query, fields='files(id, name, mimeType)').execute()
                bestanden = res.get('files', [])

                if bestanden:
                    f = bestanden[0]
                    b_id, b_mime, b_real_naam = f['id'], f['mimeType'], f['name']
                    st.session_state.bron_details.append({"naam": b_real_naam, "id": b_id})

                    try:
                        req = drive_service.files().get_media(fileId=b_id)
                        f_data = req.execute()
                        img = Image.open(io.BytesIO(f_data))
                        if img.mode != 'RGB': img = img.convert('RGB')
                        img.thumbnail((800, 800))
                        img_byte_arr = io.BytesIO()
                        img.save(img_byte_arr, format='JPEG', quality=70)

                        img_part = types.Part.from_bytes(data=img_byte_arr.getvalue(), mime_type='image/jpeg')
                        onderzoeks_payload.append(f"\n--- DOCUMENT: {b_real_naam} ---")
                        onderzoeks_payload.append(img_part)
                    except Exception:
                        pass

        # Rapport genereren
        with st.spinner("Rapport genereren..."):
            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
            except Exception as e:
                st.error(f"Fout tijdens analyse: {e}")

# ------------------------------------------------------------------------------
# 5. WEERGAVE RESULTATEN
# ------------------------------------------------------------------------------
if st.session_state.bron_details:
    st.markdown("---")
    aantal_dossiers = len(st.session_state.bron_details)
    st.subheader(f"🖼️ Geselecteerde Archiefdocumenten ({aantal_dossiers} dossiers)")
    st.caption("Klik op een tegel om het document/boek te openen in de viewer.")

    cols = st.columns(6)
    for idx, bron in enumerate(st.session_state.bron_details):
        b_naam = bron["naam"]
        b_id = bron["id"]
        thumbnail_url = f"https://drive.google.com/thumbnail?id={b_id}&sz=w400"
        drive_view_url = f"https://drive.google.com/file/d/{b_id}/view"

        col = cols[idx % 6]
        with col:
            st.markdown(f"""
                <div class="doc-card">
                    <a href="{drive_view_url}" target="_blank">
                        <img src="{thumbnail_url}" alt="{b_naam}">
                    </a>
                    <div class="doc-title">{b_naam}</div>
                </div>
            """, unsafe_allow_html=True)

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
