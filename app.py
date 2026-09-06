import io
import time
import string
import logging
import warnings
import json
import re
import gc
import math
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
APP_VERSION = "v1.2.3 (Herstelde PDF & Familie Matching)"
APP_DATE = "2026"

logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

def natuurlijke_sortering(item):
    tekst = item.get('naam', '') if isinstance(item, dict) else str(item)
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', tekst)]

def normaliseer_tekst(tekst):
    """Zet tekst om naar lowercase en vangt spellingsvariaties op (zoals ij <-> y)."""
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
    kandidaten = [
        'gemini-flash-latest',
        'gemini-flash-lite-latest'
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
                    st.info(f"⏳ Google Gemini servers zijn druk of limiet bereikt. Pauze van {wachttijd} seconden...")
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
if "uitgebreide_zoektermen" not in st.session_state:
    st.session_state.uitgebreide_zoektermen = []

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE & ZIJBALK
# ------------------------------------------------------------------------------
st.set_page_config(page_title="RBC Archief zoekmachine", page_icon="🔍", layout="wide")

with st.sidebar:
    st.title("ℹ️ Help & Info")
    with st.expander("🚨 Belangrijke informatie & Foutmeldingen"):
        st.markdown("""
        **1. Afbeeldingen/PDF laden niet?** Zorg dat de bestanden in Google Drive zijn ingesteld op **'Iedereen met de link kan bekijken'**.

        **2. Rood blok met foutmelding (bijv. 429 RESOURCE_EXHAUSTED)?** Wacht 1-2 minuten voor het opnieuws proberen.
        """)

col_title, col_ver = st.columns([4, 1])
with col_title:
    st.title("🔍 RBC Archief zoekmachine")
with col_ver:
    st.caption(f"**Versie:** `{APP_VERSION}` ({APP_DATE})")

if MODEL_NAAM:
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")
else:
    st.error("Kon geen werkend Gemini-model vinden.")
    st.stop()

col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: wanneer overleed emile delvoie?',
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
    st.
