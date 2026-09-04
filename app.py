import io
import time
import string
import logging
import warnings
import streamlit as st
from PIL import Image
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from google import genai
from google.genai import types

# ------------------------------------------------------------------------------
# APP VERSIEBEHEER
# ------------------------------------------------------------------------------
APP_VERSION = "v2.2.1"
APP_DATE = "2026"

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
    """Voert een API-call uit met wachttijd bij drukte of quota-limieten."""
    for poging in range(max_retries):
        try:
            return client.models.generate_content(model=model, contents=contents)
        except Exception as e:
            err_msg = str(e)
            if "503" in err_msg or "UNAVAILABLE" in err_msg or "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
                if poging < max_retries - 1:
                    wachttijd = 15 * (poging + 1)
                    st.info(f"⏳ Google Gemini servers zijn druk of limiet bereikt ({'503 Overbelast' if '503' in err_msg else '429 Limiet'}). Automatische pauze van {wachttijd} seconden voor poging {poging + 2}/{max_retries}...")
                    time.sleep(wachttijd)
                    continue
                else:
                    st.error("⚠️ De limiet voor de Gemini API is tijdelijk bereikt of de servers zijn te druk. Wacht even 1-2 minuten en probeer het opnieuw.")
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
if "start_zoekopdracht" not in st.session_state:
    st.session_state.start_zoekopdracht = False

# ------------------------------------------------------------------------------
# 3. STREAMLIT INTERFACE & ZIJBALK MET UITKLAPBARE INFO
# ------------------------------------------------------------------------------
st.set_page_config(page_title="RBC Archief zoekmachine", page_icon="🔍", layout="wide")

# ZIJBALK: Overzichtelijk met uitklapbare help-secties
with st.sidebar:
    st.title("ℹ️ Help & Info")
    
    with st.expander("🚨 Belangrijke informatie & Foutmeldingen"):
        st.markdown("""
        **1. Rood blok met foutmelding (bijv. 429 RESOURCE_EXHAUSTED)?**  
        Deze zoekmachine maakt gebruik van een gratis AI-model met een dagelijks limiet op het aantal zoekopdrachten. Krijg je een melding over 'quota' of 'rate-limit'? Dan is het maximale aantal AI-scans voor vandaag bereikt. Probeer je zoekopdracht morgen opnieuw; de teller wordt elke 24 uur automatisch gereset!

        **2. Houd rekening met mogelijke fouten in de AI-analyse**  
        De gratis variant gebruikt een lichter AI-model dat minder diepgaand kan redeneren of complexe documentstructuren soms verkeerd begrijpt. De AI kan hierdoor incidenteel een verkeerde datum, naam of conclusie trekken. Controleer cruciale informatie daarom altijd even in het originele archiefdocument!
        """)

    with st.expander("💡 Tips voor het testen"):
        st.markdown("""
        * **Stel specifieke vragen:** Probeer de vraag niet te algemeen te maken (zoals *"Geef alle informatie over RBC"*). Bij een te brede vraag worden er erg veel documenten gevonden. Vragen naar specifieke namen, jaartallen, boektitels of onderwerpen werken het snelst en het beste.
        * **Knop 'Voer onderzoek uit':** Hiermee start je de zoekopdracht. De AI gaat dan direct de relevante documenten en afbeeldingen analyseren.
        * **Knop 'Stop / Annuleer':** Mocht een zoekopdracht te lang duren of wil je halverwege stoppen, dan kun je hiermee het proces meteen afbreken.
        * **Schuifregelaar 'Max dossiers (Document_ID's)':** Hiermee bepaal je hoeveel verschillende archiefmappen/boeken de AI maximaal mag bekijken.
        """)

# Titel & Versie-informatie op de hoofdpagina
col_title, col_ver = st.columns([4, 1])
with col_title:
    st.title("🔍 RBC Archief zoekmachine")
with col_ver:
    st.caption(f"**Versie:** `{APP_VERSION}` ({APP_DATE})")

if MODEL_NAAM:
    st.caption(f"Actief AI-model: `{MODEL_NAAM}`")
else:
    st.error("Kon geen werkend Gemini-model vinden voor deze API-sleutel. Controleer je Gemini API key.")
    st.stop()

# Invoer van de onderzoeksvraag & parameters
col1, col2 = st.columns([3, 1])
with col1:
    onderzoeksvraag = st.text_area(
        "Vraag:",
        placeholder='Bijv: Wat staat er in het boek van Mathieu Rutten over de elektriciteitscentrale in Tongeren?',
        height=100
    )
with col2:
    max_dossiers = st.slider("Max dossiers (Document_ID's):", min_value=5, max_value=50, value=15, step=5)

# Knoppenbalk met Actie- en Stop-knoppen
btn_col1, btn_col2 = st.columns([2, 1])
with btn_col1:
    submit_button = st.button("🔍 Voer onderzoek uit", type="primary", use_container_width=True)
with btn_col2:
    stop_button = st.button("⛔ Stop / Annuleer", type="secondary", use_container_width=True)

# LEEGMAKEN EN SCHERM RESETTEN BIJ KLIK OP ZOEKEN
if submit_button:
    st.session_state.gestopt = False
    st.session_state.actieve_chat = None
    st.session_state.chat_historie = []
    st.session_state.bron_details = []
    st.session_state.start_zoekopdracht = True
    st.rerun()

# Directe stop-afhandeling
if stop_button:
    st.session_state.gestopt = True
    st.session_state.start_zoekopdracht = False
    st.warning("⚠️ Onderzoek is direct geannuleerd.")
    st.stop()

# ------------------------------------------------------------------------------
# 4. ONDERZOEKSLOGICA
# ------------------------------------------------------------------------------
if st.session_state.start_zoekopdracht:
    if not onderzoeksvraag.strip():
        st.warning("Voer a.u.b. een onderzoeksvraag in.")
        st.session_state.start_zoekopdracht = False
    else:
        # STAP 1: Inhoudsopgave scannen uit Google Sheet
        with st.spinner("Stap 1/3: Inhoudsopgave (Google Sheet) scannen..."):
            try:
                sh = gc.open(SHEET_NAAM)
                worksheet = sh.sheet1
                alle_records = worksheet.get_all_records()
                
                # Filter lege rijen uit
                data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
            except Exception as e:
                st.error(f"Kon de Google Sheet niet openen: {e}")
                st.session_state.start_zoekopdracht = False
                st.stop()

            if not data:
                st.error("De Google Sheet bevat geen geldige gegevens.")
                st.session_state.start_zoekopdracht = False
                st.stop()

            if st.session_state.gestopt:
                st.session_state.start_zoekopdracht = False
                st.stop()

            geselecteerde_doc_ids = []

            # ------------------------------------------------------------------
            # SLIMME RELEVANTIE-SCORING (WEIGHTED MATCHING + BOEK & FAMILIE BOOST)
            # ------------------------------------------------------------------
            negeer_woorden = [
                'geef', 'alle', 'over', 'radio', 'model', 'voor', 'naar', 'van', 'informatie', 
                'weet', 'welke', 'zoek', 'vind', 'wat', 'is', 'de', 'het', 'een', 'wanneer', 
                'overleed', 'gestorven', 'waar', 'wie', 'hoe', 'quand', 'où', 'geboren', 'overleden',
                'dossier', 'document', 'archief', 'toon', 'laat', 'zien', 'hebt', 'gehad', 'expliciet',
                'boek', 'publicatie', 'staan', 'vertel', 'me', 'geschreven', 'door'
            ]
            
            schoon_vraag = onderzoeksvraag.translate(str.maketrans('', '', string.punctuation))
            ruwe_woorden = [w.lower() for w in schoon_vraag.split() if len(w) > 2 and w.lower() not in negeer_woorden]
            
            zoek_groepen = []
            for w in ruwe_woorden:
                varianten = [w]
                if w == 'emiel': varianten.append('emile')
                elif w == 'emile': varianten.append('emiel')
                elif w == 'jan': varianten.append('jean')
                elif w == 'jean': varianten.append('jan')
                zoek_groepen.append(varianten)

            doc_scores = {}

            if zoek_groepen:
                familie_termen = ['familie', 'stamboom', 'geslacht', 'ouders', 'kinderen', 'echtgenoot', 'echtgenote', 'huwelijk', 'boek', 'auteur']

                for row in data:
                    doc_id_val = str(row.get('Document_ID', '')).strip()
                    b_naam_val = str(row.get('Bestandsnaam', '')).strip()
                    personen_val = str(row.get('Genoemde Personen', '') or row.get('Genoemde personen', '')).strip()
                    onderwerp_val = str(row.get('Onderwerp (NL)', '') or row.get('Onderwerp', '')).strip()
                    inhoud_val = str(row.get('Inhoud & Cijfers (NL)', '') or row.get('Inhoud & cijfers (NL)', '') or row.get('Inhoud', '')).strip()
                    
                    combi_tekst = f"{doc_id_val} {b_naam_val} {personen_val} {onderwerp_val} {inhoud_val}".lower()
                    
                    gekozen_id = doc_id_val if doc_id_val else f"SINGLE_{b_naam_val}"
                    if not gekozen_id:
                        continue

                    matched_groepen_count = 0
                    score = 0
                    
                    for grp in zoek_groepen:
                        if any(v in combi_tekst for v in grp):
                            matched_groepen_count += 1
                            score += 5

                    if matched_groepen_count == len(zoek_groepen):
                        score += 10

                    if any(v in combi_tekst for grp in zoek_groepen for v in grp if len(v) > 3):
                        if any(fam_term in combi_tekst for fam_term in familie_termen):
                            score += 8

                    if score > 0:
                        doc_scores[gekozen_id] = doc_scores.get(gekozen_id, 0) + score

                gesorteerde_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
                geselecteerde_doc_ids = [doc_id for doc_id, score in gesorteerde_docs[:max_dossiers]]

            # Fallback via Gemini bij geen directe matches
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
                            "Inhoud": set(),
                            "Paginas": 0
                        }
                    
                    dossier_samenvattingen[doc_id]["Paginas"] += 1
                    
                    pers_val = row.get('Genoemde Personen') or row.get('Genoemde personen')
                    if pers_val:
                        dossier_samenvattingen[doc_id]["Personen"].add(str(pers_val).strip())
                        
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
Jij bent een zeer strenge en nauwkeurige hoofdarchivaris. Hieronder staat een overzicht van de unieke dossiers (Document_ID's) in ons archief:

{index_tekst}

ONDERZOEKSVRAAG: "{onderzoeksvraag}"

CRITISCHE SELECTIECRITERIA:
1. Selecteer UITSLUITEND dossiers (Document_ID's) die DIRECT te maken hebben met de specifieke personen, boeken, merken of vragen.
2. Geef maximaal {max_dossiers} relevante Document_ID's terug.
3. ALLES OF NIETS: Als er geen enkel dossier specifiek relevant is, antwoord dan UITSLUITEND met het woord: GEEN_MATCH.

Geef UITSLUITEND de exacta Document_ID's terug gescheiden door komma's, OF het woord GEEN_MATCH.
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
                    st.error(f"Fout tijdens het scannen van de index ({MODEL_NAAM}): {e}")
                    st.session_state.start_zoekopdracht = False
                    st.stop()

        if not geselecteerde_doc_ids:
            st.warning("⚠️ Geen relevante documenten of boeken gevonden in het archief voor deze zoekopdracht.")
            st.session_state.start_zoekopdracht = False
            st.stop()

        # STAP 1.5: Verzamel alle gekoppelde bestanden en gegevens per dossier uit de Sheet
        eind_bestanden_lijst = []
        sheet_dossier_data = []

        for row in data:
            doc_id = str(row.get('Document_ID', '')).strip()
            b_naam = str(row.get('Bestandsnaam', '')).strip()

            if any(doc_id.lower() == g_id.lower() or b_naam.lower() == g_id.lower() for g_id in geselecteerde_doc_ids):
                sheet_dossier_data.append(row)
                if b_naam and b_naam not in eind_bestanden_lijst:
                    eind_bestanden_lijst.append(b_naam)

        # DEBUG MELDING: Details van geselecteerde documenten
        with st.expander("🔍 Bekijk details van de geselecteerde documenten uit de Sheet", expanded=True):
            st.write(f"**Geselecteerde Document_ID's ({len(geselecteerde_doc_ids)}):** `{geselecteerde_doc_ids}`")
            st.write(f"**Aantal gekoppelde pagina's/bestanden ({len(eind_bestanden_lijst)}):** `{len(eind_bestanden_lijst)} items`")

        if not eind_bestanden_lijst and not sheet_dossier_data:
            st.error("Geen geldige bestanden gekoppeld aan de geselecteerde ID's in de Google Sheet.")
            st.session_state.start_zoekopdracht = False
            st.stop()

        # STAP 2 & 3: Slimme verwerking afhankelijk van de grootte van de geselecteerde dossiers
        MAX_FOTO_LIMIET = 10

        onderzoeks_payload = [
            f"""Jij bent een financieel-historisch expert en archivaris.
Beantwoord onderstaande onderzoeksvraag grondig, helder en gedetailleerd op basis van het beschikbare materiaal.

ONDERZOEKSVRAAG: {onderzoeksvraag}

INSTRUCTIES VOOR JE RAPPORT:
1. Richt je specifiek op de gevraagde firma, personen, boeken, modellen en periode.
2. Structureer je antwoord helder met duidelijke kopjes.
3. Vermeld alle concrete namen, functies, cijfers, paginanummers en historische feiten.
4. Trek een duidelijke, gedetailleerde conclusie als antwoord op de vraag.
"""
        ]

        if len(eind_bestanden_lijst) > MAX_FOTO_LIMIET:
            # GROOT DOSSIER / BOEK: Gebruik de rijke Sheet-data voor analyse
            with st.spinner(f"Stap 2/3: Groot dossier/boek gedetecteerd ({len(eind_bestanden_lijst)} pagina's). Gegevens bundelen uit Sheet..."):
                tekst_gebundeld = f"\n--- DOSSIER INHOUD (TOTAAL {len(sheet_dossier_data)} PAGINA'S/RIJEN UIT SHEET) ---\n"
                for idx, r in enumerate(sheet_dossier_data, start=1):
                    doc_id = r.get('Document_ID', '')
                    b_naam = r.get('Bestandsnaam', '')
                    datum = r.get('Datum Document', '')
                    personen = r.get('Genoemde Personen') or r.get('Genoemde personen', '')
                    onderwerp = r.get('Onderwerp (NL)', '')
                    inhoud = r.get('Inhoud & Cijfers (NL)') or r.get('Inhoud & cijfers (NL)') or r.get('Inhoud', '')
                    
                    tekst_gebundeld += f"\n[Pagina/Item {idx}] Bestand: {b_naam} | Doc_ID: {doc_id} | Datum: {datum}\n"
                    if personen: tekst_gebundeld += f"  - Personen: {personen}\n"
                    if onderwerp: tekst_gebundeld += f"  - Onderwerp: {onderwerp}\n"
                    if inhoud: tekst_gebundeld += f"  - Inhoud & Details: {inhoud}\n"

                onderzoeks_payload.append(tekst_gebundeld)
                
                # --------------------------------------------------------------
                # VERBETERDE OMSLAG-SELECTIE: PAKT SPECIFIEK HET ALLEREERSTE BESTAND
                # VAN HET HOOGST SCORENDE DOSSIER (BIJV. DE OMSLAG VAN DOC_0001)
                # --------------------------------------------------------------
                top_doc_id = geselecteerde_doc_ids[0] if geselecteerde_doc_ids else None
                eerste_bestand = None

                if top_doc_id:
                    for r in sheet_dossier_data:
                        d_id = str(r.get('Document_ID', '')).strip()
                        b_n = str(r.get('Bestandsnaam', '')).strip()
                        if d_id.lower() == top_doc_id.lower() and b_n:
                            eerste_bestand = b_n
                            break

                if not eerste_bestand and eind_bestanden_lijst:
                    eerste_bestand = eind_bestanden_lijst[0]

                if eerste_bestand:
                    b_naam_schoon = eerste_bestand.split('/')[-1]
                    query_ref = f"name = '{b_naam_schoon}' and trashed = false"
                    res_ref = drive_service.files().list(q=query_ref, fields='files(id, name, mimeType)').execute().get('files', [])
                    if res_ref:
                        st.session_state.bron_details.append({
                            "naam": res_ref[0]['name'],
                            "id": res_ref[0]['id'],
                            "mime": res_ref[0]['mimeType']
                        })

        else:
            # KLEIN DOSSIER: Haal originele afbeeldingen/PDF's op uit Drive
            with st.spinner(f"Stap 2/3: Originele bestanden ophalen uit Drive ({len(eind_bestanden_lijst)} bestanden)..."):
                geladen_aantal = 0
                missing_files = []

                for b_naam in eind_bestanden_lijst:
                    if st.session_state.gestopt:
                        st.warning("Onderzoek geannuleerd bij het ophalen van bestanden.")
                        st.session_state.start_zoekopdracht = False
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
                        missing_files.append(b_naam_schoon)

                if missing_files:
                    st.warning(f"⚠️ De volgende bestanden uit de Sheet kon de app niet in Google Drive vinden: {missing_files}")

        # STAP 3: AI-analyse door Gemini
        with st.spinner("Stap 3/3: Historische analyse uitvoeren via Gemini..."):
            if st.session_state.gestopt:
                st.warning("Onderzoek geannuleerd voor de AI-analyse.")
                st.session_state.start_zoekopdracht = False
                st.stop()

            try:
                st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
                analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, onderzoeks_payload)
                st.session_state.chat_historie.append(("assistant", analyse_response.text))
            except Exception as e:
                st.error(f"Fout tijdens Gemini analyse: {e}")

        # Zoekopdracht afgerond
        st.session_state.start_zoekopdracht = False

# ------------------------------------------------------------------------------
# 5. WEERGAVE BRONNEN MET PREVIEWS & RAPPORT
# ------------------------------------------------------------------------------
if not st.session_state.start_zoekopdracht:
    if st.session_state.bron_details:
        st.subheader("📁 Geselecteerde bronnen / Omslag:")
        
        cols = st.columns(3)
        for index, bron in enumerate(st.session_state.bron_details):
            b_naam = bron["naam"]
            b_id = bron["id"]
            
            thumbnail_url = f"https://drive.google.com/thumbnail?id={b_id}&sz=w800"
            drive_view_url = f"https://drive.google.com/file/d/{b_id}/view"

            with cols[index % 3]:
                with st.expander(f"📄 {b_naam}", expanded=True):
                    st.image(thumbnail_url, caption=b_naam, use_container_width=True)
                    st.link_button("🔍 Open origineel in Google Drive", drive_view_url)

    if st.session_state.chat_historie:
        st.divider()
        st.subheader("📑 Historisch Onderzoeksrapport")
        
        for rol, tekst in st.session_state.chat_historie:
            with st.chat_message(rol):
                st.write(tekst)

        # Vervolgvragen stellen
        if vervolgvraag := st.chat_input("Stel een vervolgvraag over dit rapport of boek..."):
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
