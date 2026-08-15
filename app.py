# ==============================================================================
# INTERACTIEF ARCHIEF-ONDERZOEKSSCHERM (COLAB V2.6 - CHRONOLOGISCHE CONCLUSIES)
# ==============================================================================

import io
import time
import logging
import warnings
from PIL import Image
import gspread
from google.auth import default
from google.colab import auth
from googleapiclient.discovery import build
from google import genai
from google.genai import types
from getpass import getpass
import ipywidgets as widgets
from IPython.display import display, clear_output, HTML

# Silencer voor SDK-waarschuwingen
logging.getLogger("google_genai").setLevel(logging.ERROR)
warnings.filterwarnings("ignore")

# 1. AUTHENTICATIE & DRIVE/SHEETS VERBINDEN
auth.authenticate_user()
creds, _ = default()
drive_service = build('drive', 'v3', credentials=creds)
gc = gspread.authorize(creds)

try:
    from google.colab import userdata
    API_KEY = userdata.get('GEMINI_API_KEY')
except Exception:
    API_KEY = None

if not API_KEY:
    API_KEY = getpass("Plak hier je Gemini API key en druk op Enter: ")

ai_client = genai.Client(api_key=API_KEY)

# CONFIGURATIE
DRIVE_MAP_NAAM = "archieven"
SHEET_NAAM = f"Inhoudsopgave_{DRIVE_MAP_NAAM}"

# DYNAMISCHE MODEL DETECTOR
def bepaal_actief_model(client):
    kandidaten = [
        'gemini-2.0-flash-lite',
        'gemini-2.0-flash',
        'gemini-1.5-flash-002',
        'gemini-1.5-flash',
        'gemini-1.5-pro-002'
    ]
    
    try:
        voorradig = [m.name.replace("models/", "") for m in client.models.list()]
        for m in voorradig:
            if m not in kandidaten and 'gemini' in m:
                kandidaten.append(m)
    except Exception:
        pass

    for m in kandidaten:
        try:
            client.models.generate_content(model=m, contents="ping")
            return m
        except Exception:
            continue

    return 'gemini-2.0-flash-lite'

MODEL_NAAM = bepaal_actief_model(ai_client)
print(f"✓ Actief en werkend AI-model gedetecteerd: {MODEL_NAAM}")

# Globale variabelen
actieve_chat = None

# HULPFUNCTIE VOOR RETRY / RATE LIMITS (429)
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
                print(f"⚠️ API Limiet bereikt. Wachten voor {wait_time}s...")
                time.sleep(wait_time)
            else:
                raise e
    raise Exception("Max retries overschreden wegens API limieten.")

# 2. ONDERZOEKSSCRIPT FUNCTIES
def voer_archief_onderzoek_uit(onderzoeksvraag, max_bestanden):
    global actieve_chat
    if not onderzoeksvraag.strip():
        print("Voer a.u.b. een vraag in.")
        return

    print("="*60)
    print(f" ONDERZOEKSVRAAG: {onderzoeksvraag}")
    print(f" MAXIMAAL AANTAL BRONNEN: {max_bestanden}")
    print("="*60)
    print("\n[STAP 1/3] Inhoudsopgave (Google Sheet) scannen...")

    try:
        sh = gc.open(SHEET_NAAM)
        worksheet = sh.sheet1
        alle_records = worksheet.get_all_records()
        
        # Filter lege rijen uit
        data = [row for row in alle_records if str(row.get('Bestandsnaam', '')).strip()]
    except Exception as e:
        print(f"[FOUT] Kon de Google Sheet niet openen: {e}")
        return

    if not data:
        print("[FOUT] De Google Sheet bevat geen data of alleen lege rijen.")
        return

    # Directe bestandsnaam controle
    geselecteerde_bestanden = []
    for row in data:
        b_naam_sheet = str(row.get('Bestandsnaam', '')).strip()
        if b_naam_sheet and b_naam_sheet.lower() in onderzoeksvraag.lower():
            geselecteerde_bestanden.append(b_naam_sheet)

    # Indien geen directe bestandsnaam match, filteren via AI
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

        try:
            res_filter = stuur_met_retry(ai_client, filter_prompt, is_chat=False)
            geselecteerde_bestanden = [b.strip() for b in res_filter.text.split(',') if b.strip()]
        except Exception as e:
            print(f"[FOUT] Fout bij filteren index: {e}")
            return

    print(f"\n[STAP 2/3] Relevant verklaarde archiefstukken ({len(geselecteerde_bestanden)} stuks):")
    for b in geselecteerde_bestanden:
        print(f"  • {b}")

    if not geselecteerde_bestanden:
        print("\nGeen relevante bestanden gevonden op basis van de zoekopdracht.")
        return

    print("\n[STAP 3/3] Originele documenten & foto's ophalen uit Google Drive...")

    onderzoeks_payload = [
        f"""Jij bent een financieel-historisch expert en hoofdarchivaris.
Beantwoord onderstaande onderzoeksvraag grondig en nauwkeurig op basis van de meegeleverde originele archiefstukken en/of foto's.

ONDERZOEKSVRAAG: {onderzoeksvraag}

CRUCIALE INSTRUCTIES VOOR STRUCTUUR EN CONCLUSIE:
1. RESPECTEER STRICKT DE CHRONOLOGIE EN WIJZIGINGEN:
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
    for b_naam in geselecteerde_bestanden:
        b_naam_schoon = b_naam.strip("'\" ")
        if ":" in b_naam_schoon:
            b_naam_schoon = b_naam_schoon.split(":", 1)[-1].strip()

        # Ophalen via Google Drive API
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
                    display(HTML(f'📄 <b>{b_real_naam}</b> &nbsp;&nbsp;<a href="{drive_view_url}" target="_blank" style="padding: 3px 8px; background-color: #007bff; color: white; text-decoration: none; border-radius: 4px; font-size: 12px;">🔍 Open Google Doc</a><br><br>'))

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
                    display(HTML(f'📕 <b>{b_real_naam}</b> &nbsp;&nbsp;<a href="{drive_view_url}" target="_blank" style="padding: 3px 8px; background-color: #007bff; color: white; text-decoration: none; border-radius: 4px; font-size: 12px;">🔍 Open PDF in Google Drive</a><br><br>'))

                # 3. AFBEELDINGEN & FOTO'S (MET KNOP NAAR DRIVE)
                else:
                    req = drive_service.files().get_media(fileId=b_id)
                    f_data = req.execute()

                    img = Image.open(io.BytesIO(f_data))
                    if img.mode != 'RGB':
                        img = img.convert('RGB')

                    # Visuele voorbeeldweergave (Thumbnail)
                    thumb = img.copy()
                    thumb.thumbnail((250, 250))
                    print(f"\n📷 Preview: {b_real_naam}")
                    display(thumb)
                    
                    # Knop toevoegen om in hoge resolutie te openen
                    display(HTML(f'<div style="margin-top: 4px; margin-bottom: 15px;"><a href="{drive_view_url}" target="_blank" style="padding: 5px 10px; background-color: #007bff; color: white; text-decoration: none; border-radius: 4px; font-size: 12px; font-weight: bold;">🔍 Open in hoge resolutie</a></div>'))

                    # Geoptimaliseerd voor Gemini
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
                print(f"[WAARSCHUWING] Kon {b_real_naam} niet laden: {e}")
        else:
            print(f"[WAARSCHUWING] Bestand '{b_naam_schoon}' niet gevonden in Drive.")

    print(f"\n[DIEPE ANALYSE STARTEN] Analyseren van {geladen_aantal} originele documenten/foto's via {MODEL_NAAM}...")

    if geladen_aantal == 0:
        print("[FOUT] De geselecteerde bestanden konden niet worden teruggevonden in Google Drive.")
        return

    # CHAT-SESSIE STARTEN
    actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
    analyse_response = stuur_met_retry(actieve_chat, onderzoeks_payload, is_chat=True)

    print("\n" + "="*60)
    print("               HISTORISCH ONDERZOEKSRAPPORT")
    print("="*60 + "\n")
    print(analyse_response.text)

    vervolg_box.layout.display = 'block'

def stuur_vervolgvraag(b):
    global actieve_chat
    vraag = vervolg_input.value.strip()
    if not vraag:
        return

    if actieve_chat is None:
        print("[FOUT] Voer eerst een hoofdonderzoek uit.")
        return

    vervolg_input.value = ""

    with output_venster:
        print(f"\n" + "="*60)
        print(f"💬 VERVOLGVRAAG: {vraag}")
        print("="*60)
        print("Analyseren...")

        response = stuur_met_retry(actieve_chat, vraag, is_chat=True)
        print("\n" + response.text + "\n")

def wis_hoofdvraag(b):
    vraag_input.value = ""

# 3. INTERACTIEVE WIDGETS
vraag_input = widgets.Text(
    value='',
    placeholder='Bijv: Geef me de bestuursleden van Radio Belge de Construction in 1936',
    description='Vraag:',
    disabled=False,
    layout=widgets.Layout(width='55%')
)

wis_knop = widgets.Button(
    description='❌',
    tooltip='Wis de getypte vraag',
    button_style='',
    layout=widgets.Layout(width='42px', margin='0px 10px 0px 0px')
)
wis_knop.on_click(wis_hoofdvraag)

aantal_slider = widgets.IntSlider(
    value=15,
    min=5,
    max=50,
    step=5,
    description='Max bronnen:',
    disabled=False,
    continuous_update=False,
    orientation='horizontal',
    readout=True,
    readout_format='d',
    layout=widgets.Layout(width='30%')
)

zoek_knop = widgets.Button(
    description='Voer onderzoek uit',
    button_style='primary',
    icon='search'
)

output_venster = widgets.Output()

# Vervolgvraag Widgets
vervolg_input = widgets.Text(
    placeholder='Stel een vervolgvraag over dit rapport of de documenten...',
    description='Vervolg:',
    layout=widgets.Layout(width='70%')
)

vervolg_knop = widgets.Button(
    description='Verstuur vraag',
    button_style='success',
    icon='comment'
)

vervolg_box = widgets.VBox([
    widgets.HTML(value="<br><b>💬 STEL EEN VOLGENDE VERVOLGVRAAG:</b>"),
    widgets.HBox([vervolg_input, vervolg_knop])
], layout=widgets.Layout(display='none'))

def on_knop_clicked(b):
    vervolg_box.layout.display = 'none'
    with output_venster:
        clear_output()
        voer_archief_onderzoek_uit(vraag_input.value, aantal_slider.value)

# ACTIES KOPPELEN
zoek_knop.on_click(on_knop_clicked)
vraag_input.on_submit(on_knop_clicked)

vervolg_knop.on_click(stuur_vervolgvraag)
vervolg_input.on_submit(stuur_vervolgvraag)

# OPBOUW SCHERMLAY-OUT
print("--- ARCHIEFVRAAG STELLEN ---")
display(widgets.HBox([vraag_input, wis_knop, aantal_slider]))
display(zoek_knop)
display(output_venster)
display(vervolg_box)
