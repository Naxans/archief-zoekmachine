# ==============================================================================
# INTERACTIEF ARCHIEF-ONDERZOEKSSCHERM (LEEG INVOERVELD + ENTER-TOETS)
# ==============================================================================

import io
import time
from PIL import Image
import gspread
from google.auth import default
from google.colab import auth
from googleapiclient.discovery import build
from google import genai
from google.genai import types
from getpass import getpass
import ipywidgets as widgets
from IPython.display import display, clear_output

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
MODEL_NAAM = 'gemini-flash-lite-latest'

# Globale variabelen
actieve_chat = None

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
        data = worksheet.get_all_records()
    except Exception as e:
        print(f"[FOUT] Kon de Google Sheet niet openen: {e}")
        return

    if not data:
        print("[FOUT] De Google Sheet bevat nog geen data of is leeg.")
        return

    index_tekst = ""
    for row in data:
        index_tekst += (
            f"Bestand: {row.get('Bestandsnaam')} | "
            f"Datum: {row.get('Datum Document')} | "
            f"Personen: {row.get('Genoemde Personen')} | "
            f"Onderwerp: {row.get('Onderwerp (NL)')} | "
            f"Samenvatting: {row.get('Inhoud & Cijfers (NL)')}\n"
        )

    filter_prompt = f"""
    Jij bent hoofdarchivaris. Hieronder staat de inhoudsopgave van ons archief:

    {index_tekst}

    ONDERZOEKSVRAAG: "{onderzoeksvraag}"

    INSTRUCTIES:
    1. Welke bestanden/foto's uit de index zijn het meest relevant voor deze specifieke vraag?
    2. Let heel goed op het specifieke BEDRIJF, de PERSONEN en de PERIODE/JAARTALLEN genoemd in de vraag.
    3. Als een gekozen pagina aangeeft dat een balans/akte doorloopt, selecteer dan OOK de bijbehorende pagina's!
    4. Geef maximaal {max_bestanden} meest relevante bestandsnamen terug.

    Geef UITSLAUITEND de bestandsnamen terug gescheiden door komma's. Geen extra tekst.
    """

    res_filter = ai_client.models.generate_content(
        model=MODEL_NAAM,
        contents=filter_prompt
    )

    geselecteerde_bestanden = [b.strip() for b in res_filter.text.split(',') if b.strip()]

    print(f"\n[STAP 2/3] Relevant verklaarde archiefstukken ({len(geselecteerde_bestanden)} stuks):")
    for b in geselecteerde_bestanden:
        print(f"  • {b}")

    if not geselecteerde_bestanden:
        print("\nGeen relevante bestanden gevonden op basis van de zoekopdracht.")
        return

    print("\n[STAP 3/3] Originele documenten ophalen uit Google Drive...")
    
    alle_drive_bestanden = {}
    page_token = None
    while True:
        res = drive_service.files().list(
            q="trashed = false",
            spaces='drive',
            pageToken=page_token,
            fields='nextPageToken, files(id, name, mimeType)'
        ).execute()
        for f in res.get('files', []):
            alle_drive_bestanden[f['name']] = f
        page_token = res.get('nextPageToken')
        if not page_token:
            break

    onderzoeks_payload = [
        f"""Jij bent een financieel-historisch expert en archivaris.
Beantwoord onderstaande onderzoeksvraag grondig en gedetailleerd op basis van de meegeleverde originele archiefstukken.

ONDERZOEKSVRAAG: {onderzoeksvraag}

INSTRUCTIES VOOR JE RAPPORT:
1. Richt je specifiek op de gevraagde firma, personen en periode.
2. Structureer je antwoord helder (bijv. per jaar of per onderwerp).
3. Vermeld alle concrete namen, functies, cijfers en details die op de documenten staan.
4. Citeer steeds de bestandsnaam (bijv. 'staatsblad1935-10-blz296.jpg') wanneer je naar specifieke informatie verwijst.
5. Trek een heldere conclusie als antwoord op de vraag.
"""
    ]

    geladen_aantal = 0
    for b_naam in geselecteerde_bestanden:
        if b_naam in alle_drive_bestanden:
            f = alle_drive_bestanden[b_naam]
            b_id = f['id']
            b_mime = f['mimeType']

            try:
                if b_mime == 'application/vnd.google-apps.document':
                    req = drive_service.files().export_media(fileId=b_id, mimeType='text/plain')
                    doc_txt = req.execute().decode('utf-8', errors='ignore')
                    onderzoeks_payload.append(f"\n--- INHOUD GOOGLE DOC ({b_naam}) ---\n{doc_txt}")
                else:
                    req = drive_service.files().get_media(fileId=b_id)
                    f_data = req.execute()

                    img = Image.open(io.BytesIO(f_data))
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    
                    img.thumbnail((1200, 1200))

                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='JPEG', quality=85)

                    img_part = types.Part.from_bytes(
                        data=img_byte_arr.getvalue(),
                        mime_type='image/jpeg'
                    )
                    onderzoeks_payload.append(f"\n--- ORIGINELE AFBEELDING: {b_naam} ---")
                    onderzoeks_payload.append(img_part)

                geladen_aantal += 1
            except Exception as e:
                print(f"[WAARSCHUWING] Kon {b_naam} niet laden: {e}")

    print(f"\n[DIEPE ANALYSE STARTEN] Analyseren van {geladen_aantal} originele documenten via Gemini...")

    if geladen_aantal == 0:
        print("[FOUT] De geselecteerde bestanden konden niet worden teruggevonden in Google Drive.")
        return

    # CHAT-SESSIE STARTEN
    actieve_chat = ai_client.chats.create(model=MODEL_NAAM)
    analyse_response = actieve_chat.send_message(onderzoeks_payload)

    print("\n" + "="*60)
    print("               HISTORISCH ONDERZOEKSRAPPORT")
    print("="*60 + "\n")
    print(analyse_response.text)

    # Vervolgvenster tonen als het rapport klaar is
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
        
        response = actieve_chat.send_message(vraag)
        
        print("\n" + response.text + "\n")

def wis_hoofdvraag(b):
    vraag_input.value = ""

# 3. INTERACTIEVE WIDGETS
# Het veld start leeg (value=""), de voorbeeldvraag staat in placeholder (grijze hinttekst)
vraag_input = widgets.Text(
    value='',
    placeholder='Bijv: Geef me de bestuursleden van de firma "Radio Belge de Construction" in het jaar 1935',
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

# ACTIES KOPPELEN (KNOPPEN + ENTER-TOETS)
zoek_knop.on_click(on_knop_clicked)
vraag_input.on_submit(on_knop_clicked)       # ENTER voor hoofdvraag

vervolg_knop.on_click(stuur_vervolgvraag)
vervolg_input.on_submit(stuur_vervolgvraag)   # ENTER voor vervolgvraag

# OPBOUW SCHERMLAY-OUT
print("--- ARCHIEFVRAAG STELLEN ---")
display(widgets.HBox([vraag_input, wis_knop, aantal_slider]))
display(zoek_knop)
display(output_venster)
display(vervolg_box)
