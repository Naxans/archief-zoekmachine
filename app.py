# ------------------------------------------------------------------------------
# 6. MULTIMODAL HISTORISCHE ANALYSE VIA GEMINI (EERSTE VRAAG OF VERVOLGVRAAG)
# ------------------------------------------------------------------------------
# A. Eerste vraag verwerken (chat start nieuw)
if st.session_state.blader_paginas and not st.session_state.chat_historie:
    with st.spinner("Stap 3/3: Originele PDF's/Afbeeldingen ophalen & Historische analyse genereren..."):
        try:
            onderzoeks_prompt = f"""
Jij bent een zeer nauwkeurige en uitputtende historisch archivarisexpert voor een Belgisch archief.
Analyseer de meegeleverde originele bestanden (PDF's / afbeeldingen) EN de metadata-samenvattingen uitermate grondig en op een deterministische, feitelijke manier.

GEBRUIKERSVRAAG: {st.session_state.huidige_vraag}

STRUCTUUREISEN VOOR HET RAPPORT:
- Geef direct en expliciet antwoord op de gestelde vraag.
- Vermeld ALLE concrete namen, bedragen, datums, tijdschriftnummers en locaties die in de bronnen voorkomen.
- Als er sprake is van betalingen of schadeclaims: vermeld zowel het totaalbedrag als de specifieke personen of posten waaraan werd uitbetaald.
- Bied bij chronologische of financiële vragen een duidelijke tijdslijn of tabeloverzicht.
- Sluit af met een korte, heldere synthese.
"""
            payload = [onderzoeks_prompt]

            sheet_data = getattr(st.session_state, 'sheet_dossier_data', [])
            tekst_gebundeld = "\n--- INHOUDSOPGAVE METADATA ---\n"
            for r in sheet_data:
                tekst_gebundeld += f"Bestand: {r.get('Bestandsnaam', '')} | Personen: {r.get('Genoemde Personen', '')} | Inhoud: {r.get('Inhoud & Cijfers (NL)', '')}\n"
            payload.append(tekst_gebundeld)

            top_dossier_ids = st.session_state.geselecteerde_doc_ids[:3]
            toegevoegde_bestanden_count = 0

            for p in st.session_state.blader_paginas:
                if p.get("doc_id") in top_dossier_ids and toegevoegde_bestanden_count < 5:
                    file_id = p.get("id")
                    file_name = p.get("naam", "").lower()
                    mime_type = p.get("mime", "")

                    if not mime_type or mime_type == 'application/octet-stream':
                        if file_name.endswith('.pdf'):
                            mime_type = 'application/pdf'
                        elif file_name.endswith('.jpg') or file_name.endswith('.jpeg'):
                            mime_type = 'image/jpeg'
                        elif file_name.endswith('.png'):
                            mime_type = 'image/png'

                    if file_id and mime_type in ['application/pdf', 'image/jpeg', 'image/png']:
                        try:
                            file_bytes = drive_service.files().get_media(fileId=file_id).execute()
                            payload.append(types.Part.from_bytes(
                                data=file_bytes,
                                mime_type=mime_type
                            ))
                            toegevoegde_bestanden_count += 1
                        except Exception as e_dl:
                            st.caption(f"Kon {file_name} niet rechtstreeks downloaden: {e_dl}")

            # Start een nieuwe chat-sessie
            st.session_state.actieve_chat = ai_client.chats.create(model=MODEL_NAAM)

            analysis_config = types.GenerateContentConfig(temperature=0.0)

            # Voer het eerste verzoek uit
            analyse_response = genereer_met_retry(ai_client, MODEL_NAAM, payload, config=analysis_config)
            
            st.session_state.chat_historie.append(("user", st.session_state.huidige_vraag))
            st.session_state.chat_historie.append(("assistant", analyse_response.text))
            gc.collect()
            st.rerun()
        except Exception as e:
            st.error(f"Fout bij historische analyse: {e}")

# B. Vervolgvraag verwerken (gebruikt de bestaande chat-sessie)
elif getattr(st.session_state, 'verwerk_vervolgvraag', False):
    with st.spinner("Vervolgvraag analyseren met nieuwe archiefcontext..."):
        try:
            # Metadata van eventuele nieuw gevonden dossiers toevoegen als extra context
            sheet_data = getattr(st.session_state, 'sheet_dossier_data', [])
            extra_context = "\n--- AANVULLENDE ARCHIEF-METADATA VOOR DEZE VERVOLGVRAAG ---\n"
            for r in sheet_data:
                extra_context += f"Bestand: {r.get('Bestandsnaam', '')} | Personen: {r.get('Genoemde Personen', '')} | Inhoud: {r.get('Inhoud & Cijfers (NL)', '')}\n"

            vervolg_prompt = f"{st.session_state.huidige_vraag}\n\n{extra_context}"

            if st.session_state.actieve_chat:
                vervolg_response = st.session_state.actieve_chat.send_message(vervolg_prompt)
                antwoord_tekst = vervolg_response.text
            else:
                # Fallback als de chat-sessie door een herlaadbeurt kwijt is
                vervolg_response = genereer_met_retry(ai_client, MODEL_NAAM, vervolg_prompt)
                antwoord_tekst = vervolg_response.text

            st.session_state.chat_historie.append(("user", st.session_state.huidige_vraag))
            st.session_state.chat_historie.append(("assistant", antwoord_tekst))
            st.session_state.verwerk_vervolgvraag = False
            gc.collect()
            st.rerun()
        except Exception as e:
            st.error(f"Fout bij verwerken vervolgvraag: {e}")
            st.session_state.verwerk_vervolgvraag = False

# ------------------------------------------------------------------------------
# 7. RAPPORT WEERGAVE & INTERACTIEVE CHAT
# ------------------------------------------------------------------------------
if st.session_state.chat_historie:
    st.divider()
    st.subheader("📑 Historisch Onderzoeksrapport & Dialoog")
    
    for rol, tekst in st.session_state.chat_historie:
        with st.chat_message(rol):
            st.write(tekst)

    # VRAAG-OP-VRAAG CHATBOX ONDERAAN HET RAPPORT
    vervolgvraag = st.chat_input("Stel een vervolgvraag (het archief wordt opnieuw doorzocht met behoud van de historie)...")
    
    if vervolgvraag:
        st.session_state.huidige_vraag = vervolgvraag
        st.session_state.start_zoekopdracht = True
        st.session_state.verwerk_vervolgvraag = True  # Vlag om aan te geven dat het om een vervolgvraag gaat!
        st.rerun()
