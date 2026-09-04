st.title("🔍 RBC Archief zoekmachine")

# Twee overzichtelijke uitklapbare vakken
with st.expander("ℹ️ Belangrijke informatie & Foutmeldingen (Slaapstand, Limieten)"):
    st.markdown("""
    **1. Staat de app in 'slaapstand'?**
    Om capaciteit te besparen gaat de zoekmachine automatisch in slaapstand. Zie je de knop *"Yes, get this app back up!"*? Klik hierop en wacht ~30 seconden. Duurt het langer? Ververs de pagina (F5).
    
    **2. Zie je een rood blok met een foutmelding (bijv. 429 RESOURCE_EXHAUSTED)?**
    Dan is het maximale dagelijkse limiet van het gratis AI-model bereikt. Probeer je zoekopdracht morgen opnieuw; de teller wordt elke 24 uur gereset.
    
    **3. Mogelijke fouten in de AI-analyse**
    De gratis variant gebruikt een lichter AI-model dat complexe documenten incidenteel verkeerd kan interpreteren. Controleer cruciale gegevens (zoals datums of namen) altijd even in het originele archiefdocument!
    """)

with st.expander("💡 Handige tips voor het testen & Gebruiksaanwijzing"):
    st.markdown("""
    * **Stel specifieke vragen:** Vragen naar specifieke namen, jaartallen of onderwerpen werken het snelst en het beste (vermijd brede vragen zoals *"Geef alle informatie over RBC"*).
    * **Knop 'Voer onderzoek uit':** Hiermee start je de analyse van relevante documenten en afbeeldingen.
    * **Knop 'Stop / Annuleer':** Mocht een zoekopdracht te lang duren, dan kun je hiermee het proces meteen afbreken.
    * **Schuifregelaar 'Max dossiers':** 
      * *Laag (5–10):* Snelle resultaten en minder AI-belasting.
      * *Hoog (15–25):* Voor ingewikkelde vragen waar de informatie verspreid ligt over meerdere mappen.
    
    *Omdat de resultaten nog niet 100% nauwkeurig zijn, verbeteren we ons digitaal archief stapsgewijs. Jouw feedback als expert is daarbij enorm waardevol!*
    """)
