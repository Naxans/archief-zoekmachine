import io
import time
import string
import logging
import warnings
import json
import re
import gc
import math
from string import Template
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
APP_VERSION = "v1.6.6 (Dossier-Aggregatie & Ruis-Filtering)"
APP_DATE = "2026"

logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

def natuurlijke_sortering(item):
    tekst = item.get('naam', '') if isinstance(item, dict) else str(item)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', tekst)]

def normaliseer_tekst(tekst):
    """Zet tekst om naar lowercase en vangt spellingsvariaties op."""
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
# 2. CONFIGURATIE & MODEL SELECTIE
# ------------------------------------------------------------------------------
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

def bepaal_werkend_model(client):
    kandidaten = ['gemini-flash-latest', 'gemini-flash-lite-latest']
    for model_naam in kandidaten:
        try:
            client.models.generate_content(model=model_naam, contents="ping")
            return model_naam
        except Exception:
            continue
    return 'gemini-flash-latest'

MODEL_NAAM = bepaal_werkend_model(ai_client)

def genereer_met_retry(client, model, contents, max_retries=4, config=None):
    """
    Genereert content via Gemini met retry bij limieten.
    """
    for poging in range(max_retries):
        try:
            if config:
                return client.models.generate_content(model=model, contents=contents, config=config)
            else:
                return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            err_msg = str(e)
            if "503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if poging < max_retries - 1:
                    wachttijd = 15 * (poging + 1)
                    st.info(f"⏳ Gemini API-limiet bereikt. Pauze van {wachttijd} seconden...")
                    time.sleep(wachttijd)
                    continue
                else:
                    st.error("⚠️ De limiet voor de Gemini API is tijdelijk bereikt. Wacht 1-2 minuten.")
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
if "specifieke_termen" not in st.session_state:
    st.session_state.specifieke_termen = []
if "generieke_termen" not in st.session_state:
    st.session_state.generieke_termen = []
if "harde_naam_targets" not in st.session_state:
    st.session_state.harde_naam_targets = []
if "synoniemen_doc_termen" not in st.session_state:
    st.session_state.synoniemen_doc_termen = []
if "genegeerde_ruis" not in st.session_state:
    st.session_state.genegeerde_ruis = []

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE
# ------------------------------------------------------------------------------
st.set_page_config(page_title="RBC Archief zoekmachine", page_icon="🔍", layout="wide")

col_title, col_ver = st.columns([4, 1])
with col_title:
    st.title("🔍 RBC Archief zoekmachine")
with col_ver:
    st.caption(f"**Versie:** `{APP_VERSION}` ({APP_DATE})")

if MODEL_NAAM:
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: hoe was de financiële toestand van de firma radio belge de construction tussen 1934 en 1940?',
        height=100
    )
with col2:
    max_dossiers = st.slider("Max dossiers (Document_ID's):", min_value=5, max_value=50, value=15, step=5)

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
    st.session_state.specifieke_termen = []
    st.session_state.generieke_termen = []
    st.session_state.harde_naam_targets = []
    st.session_state.synoniemen_doc_termen = []
    st.session_state.genegeerde_ruis = []
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
    st.warning("⚠️ Onderzoek geannuleerd.")
    st.stop()

# ------------------------------------------------------------------------------
# 4. QUERY ONTLEDING & DYNAMISCHE SCORING (DOSSIER AGGREGATIE)
# ------------------------------------------------------------------------------
if st.session_state.start_zoekopdracht:
    if not st.session_state.huidige_vraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
        st.session_state.start_zoekopdracht = False
    else:
        with st.spinner("Stap 1/3: Inhoudsopgave scannen..."):
            try:
                sh = gc_drive.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.session_state.start_zoekopdracht = False
                st.stop()

        with st.spinner("Stap 1b/3: AI-ontleding (NL + FR Synoniemen & Datums)..."):
            vraag_orig = st.session_state.huidige_vraag

            prompt_extraction = f"""
Jij bent een intelligente zoekarchivaris voor een Belgisch historisch archief uit de jaren 1930-1950. Ontleed de onderstaande zoekvraag.

GEBRUIKERSVRAAG: "{vraag_orig}"

CRUCIALE REGELS VOOR DE CATEGORIEËN:
1. "personen": Extraheer persoonsnamen EN genereer bekende spellingvariaties voor voornamen/achternamen (bijv. "emile" -> ["emile", "emiel"]).
2. "specifieke_termen": Extraheer uitsluitend de MEEST SPECIFIEKE en UNIEKE identificatierestanten, modellers, typenummers EN ALLE DATUMS/JAARTALLEN (zoals "1934", "1940", "10 mei 1940"). DATUMS EN JAARTALLEN MOGEN NOOIT ONDER RUIS VALLEN!
3. "generieke_termen":
       - Extraheer merknamen, zakelijke onderwerpen en unieke combinatiefrases.
       - ESSENTIËLE FINANCIËLE EN INHOUDELIJKE CONCEPTEN / ACTIEWOORDEN:
         Neem termen zoals "betaald", "uitbetaling", "vergoeding", "bedrag", "persoon", "naam", "grootte", "oorlogsschade", "financiële toestand", "overleed", "bestuursleden" ALTIJD op in deze lijst van generieke_termen!
4. "synoniemen_documenttypes": OMDAT BELGISCHE ARCHIEVEN UIT DIE TIJD VAAK FRANSTALIG WAREN (STAATSBLAD / MONITEUR), VOEG JE ZOWEL NEDERLANDSE ALS FRANSE SYNONIEMEN TOE.
   - Bij financiën / balansen / toestand: ["staatsblad", "moniteur", "balans", "bilan", "jaarrekening", "comptes annuels", "kapitaal", "capital", "concordaat", "concordat", "inventaris", "inventaire"]
   - Bij oprichting / statuten: ["oprichting", "statuts", "acte", "akte", "annexes", "bijlagen"]
5. "ruis_genegeerd":
       - UITSLUITEND betekenisloze grammaticale lidwoorden, voorzetsels, koppeltekens en hulpwerkwoorden van staat (zoals "hoe", "was", "de", "het", "van", "tussen", "en", "werd", "geef", "me", "dat").
       - STRIKT VERBODEN IN RUIS: Inhoudelijke woorden en begrippen zoals "betaald", "persoon", "naam", "bedrag", "grootte", "overleed" of "bestuursleden" MOGEN NOOIT ONDER RUIS VALLEN!

    WAARSCHUWING: Elk woord mag maar in EXACT EÉN categorie voorkomen!
Geef UITSLUITEND een geldig JSON-object terug:
{{
  "personen": [],
  "specifieke_termen": ["1934", "1940"],
  "generieke_termen": ["radio belge de construction", "financiële toestand"],
  "synoniemen_documenttypes": ["staatsblad", "moniteur", "balans", "bilan", "jaarrekening", "comptes annuels", "kapitaal", "capital"],
  "ruis_genegeerd": ["hoe", "was", "de", "firma", "van", "tussen", "en"]
}}
"""
            extracted_data = None
            zero_temp_config = types.GenerateContentConfig(temperature=0.0)

            try:
                res = genereer_met_retry(ai_client, MODEL_NAAM, prompt_extraction, config=zero_temp_config)
                json_match = re.search(r'\{.*\}', res.text, re.DOTALL)
                if json_match:
                    extracted_data = json.loads(json_match.group(0))
            except Exception as e:
                st.error(f"⛔ **API-Limiet of Netwerkfout bij AI Query-ontleding:**\n\n{e}")
                st.session_state.start_zoekopdracht = False
                st.stop()

            if not extracted_data:
                st.error("⛔ **AI Query-ontleding kon geen geldige data structureren.** Zoekopdracht geannuleerd.")
                st.session_state.start_zoekopdracht = False
                st.stop()

            harde_namen = [normaliseer_tekst(p) for p in extracted_data.get("personen", []) if len(p) >= 2]
            
            if 'emile' in harde_namen and 'emiel' not in harde_namen:
                harde_namen.append('emiel')
            elif 'emiel' in harde_namen and 'emile' not in harde_namen:
                harde_namen.append('emile')

            spec_termen = [normaliseer_tekst(s) for s in extracted_data.get("specifieke_termen", []) if len(s) >= 1]
            gen_termen = [normaliseer_tekst(g) for g in extracted_data.get("generieke_termen", []) if len(g) >= 2]
            
            algemene_woorden = {'firma', 'bedrijf', 'vennootschap', 'maatschappij', 'nv', 'sa', 'bv'}
            gen_termen = [gt for gt in gen_termen if gt not in algemene_woorden]

            syn_termen = [normaliseer_tekst(syn) for syn in extracted_data.get("synoniemen_documenttypes", []) if len(syn) >= 2]
            raw_ruis = [normaliseer_tekst(r) for r in extracted_data.get("ruis_genegeerd", [])]

            alle_nuttige_zoektermen = set(harde_namen + spec_termen + gen_termen + syn_termen)
            
            schone_ruis = []
            for r in raw_ruis:
                is_stiekem_zoekterm = False
                for nuttig in alle_nuttige_zoektermen:
                    if r == nuttig or (len(r) > 3 and r in nuttig.split()):
                        is_stiekem_zoekterm = True
                        break
                if not is_stiekem_zoekterm and r:
                    schone_ruis.append(r)

            st.session_state.harde_naam_targets = list(set(harde_namen))
            st.session_state.specifieke_termen = list(set(spec_termen))
            st.session_state.generieke_termen = list(set(gen_termen))
            st.session_state.synoniemen_doc_termen = list(set(syn_termen))
            st.session_state.genegeerde_ruis = list(set(schone_ruis))

        with st.spinner("Stap 2/3: Archiefstukken & PDF's matchen op inhoud..."):
            dossier_scores = {}

            specifieke_termen = st.session_state.specifieke_termen
            generieke_termen = st.session_state.generieke_termen
            synoniemen_termen = st.session_state.synoniemen_doc_termen
            harde_namen = st.session_state.harde_naam_targets

            for row in data:
                b_naam = str(row.get('Bestandsnaam', '')).strip()
                doc_id = str(row.get('Document_ID', '')).strip()
                if not doc_id:
                    doc_id = f"SINGLE_{b_naam}"

                pers = normaliseer_tekst(row.get('Genoemde Personen') or row.get('Genoemde personen') or '')
                ond = normaliseer_tekst(row.get('Onderwerp (NL)') or row.get('Onderwerp') or '')
                inhoud = normaliseer_tekst(row.get('Inhoud & Cijfers (NL)') or row.get('Inhoud & cijfers') or row.get('Inhoud') or '')
                
                b_naam_norm = normaliseer_tekst(b_naam)

                score = 0

                # 1. Personen matching
                for hn in harde_namen:
                    if hn in b_naam_norm:
                        score += 10000
                    elif hn in pers:
                        score += 8000
                    elif hn in ond or hn in inhoud:
                        score += 3000

                # 2. Specifieke termen & Jaartallen matching
                for st_term in specifieke_termen:
                    if st_term in b_naam_norm:
                        score += 25000
                    elif st_term in ond or st_term in inhoud:
                        if st_term.isdigit() and len(st_term) == 4:
                            score += 1000
                        else:
                            score += 8000

                # 3. Generieke merknamen / Inhoudsonderwerpen matching
                for gt_term in generieke_termen:
                    if gt_term in b_naam_norm:
                        score += 3000
                    elif gt_term in ond or gt_term in inhoud:
                        score += 1500

                # 4. NL + FR Synoniemen matching
                for syn_term in synoniemen_termen:
                    if syn_term in b_naam_norm:
                        score += 15000
                    elif syn_term in ond or syn_term in inhoud:
                        score += 9000

                if score > 0:
                    # AGGREGATIE LOGICA: Telt alle pagina's binnen hetzelfde dossier bij elkaar op
                    dossier_scores[doc_id] = dossier_scores.get(doc_id, 0) + score

            gesorteerde_dossiers = [d_id for d_id, sc in sorted(dossier_scores.items(), key=lambda x: x[1], reverse=True)]

            if not gesorteerde_dossiers:
                if specifieke_termen or harde_namen or generieke_termen:
                    st.warning("⚠️ Geen archiefstukken gevonden die overeenkomen met de opgegeven zoektermen.")
                    st.session_state.start_zoekopdracht = False
                    st.stop()
                else:
                    gesorteerde_dossiers = list(set(str(row.get('Document_ID', '')).strip() or f"SINGLE_{str(row.get('Bestandsnaam', '')).strip()}" for row in data))

            st.session_state.geselecteerde_doc_ids = gesorteerde_dossiers[:max_dossiers]
