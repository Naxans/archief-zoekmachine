import streamlit as st
from google import genai
import gspread
from google.oauth2.service_account import Credentials
from PIL import Image

# 1. Google Sheets verbinding via Streamlit Secrets
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

@st.cache_resource
def get_gspread_client():
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    return gspread.authorize(creds)

# 2. Gemini AI verbinding
@st.cache_resource
def get_gemini_client():
    return genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

# Paginatitel & Layout
st.set_page_config(page_title="Archief Zoekmachine", page_icon="🔍")
st.title("🔍 Archief Zoekmachine")

try:
    gc = get_gspread_client()
    client = get_gemini_client()
    st.success("Succesvol verbonden met Google Sheets en Gemini API!")
except Exception as e:
    st.error(f"Fout bij verbinden: {e}")

# Hier komt de rest van de app-functionaliteit
st.info("De basiskoppelingen werken. Je kunt nu afbeeldingen verwerken of zoekopdrachten uitvoeren.")
