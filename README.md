# 🔍 Archief Zoekmachine

Een gestroomlijnde Streamlit-toepassing die historische archiefstukken uit Google Drive analyseert met behulp van Google Gemini AI en een Google Sheets inhoudsopgave.

## 🛠️ Onderdelen & Architectuur
1. **Google Sheets (`Inhoudsopgave_archieven`)**: Bevat de index van alle bestanden (bestandsnaam, datum, personen, onderwerp).
2. **Google Drive (`archieven`)**: Bevat de originele afbeeldingen (JPEG/PNG) en documenten.
3. **GitHub Repository**: Bevat de broncode (`app.py` en `requirements.txt`).
4. **Streamlit Community Cloud**: Host de applicatie live op het web.
5. **Google Sites**: Biedt de zoekmachine aan de eindgebruikers via een `iframe`.

## ⚙️ Secrets (Streamlit Config)
In de Streamlit Cloud instellingen (`Settings > Secrets`) staan de toegangssleutels:
- `GEMINI_API_KEY`: API-sleutel van Google AI Studio.
- `gcp_service_account`: JSON-gegevens van het Google GCP Service Account.

## 🌐 Google Sites Insluiten
De app is ingesloten op Google Sites via **Invoegen > Insluiten > Insluitcode**:
```html
<iframe 
    src="https://archief-zoekmachine-ucgmgwfxzpvtvr9zk7mmvk.streamlit.app/?embed=true" 
    width="100%" 
    height="850px" 
    style="border:none;">
</iframe>
