import os
import re
import json
import pandas as pd
from typing import List, Dict, Any, Tuple
import google.generativeai as genai

# Configureren van Gemini API
genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))

class SmartArchiveRetriever:
    def __init__(self, excel_path: str):
        """
        Initialiseert de retriever met het pad naar de Google Sheet / Excel inventaris.
        """
        self.excel_path = excel_path
        self.df = self._load_data()

    def _load_data(self) -> pd.DataFrame:
        """Laadt de data en verwerkt lege waarden."""
        df = pd.read_excel(self.excel_path)
        # Zorg ervoor dat alle doorzochte kolommen als string behandeld worden
        columns_to_search = [
            'Bestandsnaam', 
            'Genoemde Personen', 'Genoemde personen', 
            'Onderwerp (NL)', 'Onderwerp', 
            'Inhoud & Cijfers (NL)', 'Inhoud & cijfers', 'Inhoud'
        ]
        for col in df.columns:
            df[col] = df[col].fillna('').astype(str)
        return df

    def analyze_query_with_gemini(self, user_query: str) -> Dict[str, Any]:
        """
        Analyseert de gebruikersvraag met Gemini.
        Extractie van kernbegrippen, jaartallen/datums (GEEN RUIS MEER) en synoniemen.
        """
        prompt = f"""
Je bent een expert-archivaris die zoekopdrachten van gebruikers analyseert voor een Belgisch historisch archief.
Analyseer de onderstaande vraag van de gebruiker en verdeel de termen in categorieën.

CRUCIALE INSTRUCTIES VOOR DATUMS EN SYNONIEMEN:
1. DATUMS & JAARTALLEN (zoals '1934', '1940', '10 mei 1940', 'jaren 30') MOETEN ALTIJD ONDER 'unieke_begrippen' OF 'generieke_begrippen' GEPLAATST WORDEN. Ze mogen NOOIT onder 'ruis_genegeerd' vallen!
2. Als de vraag gaat over financiën, status, oprichting of juridische zaken (bijv. "financiële toestand", "balans", "faillissement"), voeg dan gerelateerde synoniemen en documenttypes toe onder 'synoniemen_documenttypes' (bijv. "staatsblad", "moniteur", "balans", "jaarrekening", "kapitaal", "concordaat").
3. Onder 'ruis_genegeerd' komen ENKEL grammaticale vulwoorden (zoals 'hoe', 'was', 'de', 'van', 'het', 'tussen', 'en', 'is', 'wie').

Gebruikersvraag: "{user_query}"

Geef het antwoord UITSLUITEND terug als een geldig JSON-object met de volgende structuur:
{{
    "unieke_begrippen": ["lijst van hele specifieke namen, merknamen, typenummers en exacte jaartallen/datums"],
    "generieke_begrippen": ["lijst van bredere onderwerpen zoals 'financiële toestand', 'bedrijf', 'radio'"],
    "synoniemen_documenttypes": ["automatisch afgeleide termen zoals 'staatsblad', 'moniteur', 'balans', 'jaarrekening', 'inventaris'"],
    "ruis_genegeerd": ["lijst van grammaticale vulwoorden die geen zoekwaarde hebben"]
}}
"""
        model = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"}
        )
        
        try:
            result = json.loads(response.text)
        except Exception:
            # Fallback als JSON parsing faalt
            result = {
                "unieke_begrippen": re.findall(r'\b\d{4}\b', user_query),
                "generieke_begrippen": [user_query],
                "synoniemen_documenttypes": ["staatsblad", "balans", "jaarrekening"],
                "ruis_genegeerd": ["hoe", "was", "de", "van", "tussen", "en"]
            }
            
        return result

    def _calculate_score(self, row: pd.Series, analysis: Dict[str, Any]) -> float:
        """
        Berekent een relevantiescore voor een specifieke rij op basis van de kolommen.
        """
        score = 0.0

        # Kolomdefinities met hun alternatieven
        bestandsnaam = row.get('Bestandsnaam', '')
        personen = row.get('Genoemde Personen', row.get('Genoemde personen', ''))
        onderwerp = row.get('Onderwerp (NL)', row.get('Onderwerp', ''))
        inhoud = row.get('Inhoud & Cijfers (NL)', row.get('Inhoud & cijfers', row.get('Inhoud', '')))

        full_text = f"{bestandsnaam} {personen} {onderwerp} {inhoud}".lower()

        # 1. Unieke begrippen & Datums/Jaartallen (Zeer hoge weging: 20.000 - 25.000 punten)
        for term in analysis.get('unieke_begrippen', []):
            term_lower = term.lower()
            if not term_lower:
                continue
            
            # Match in Bestandsnaam
            if term_lower in bestandsnaam.lower():
                score += 25000
            # Match in Personen / Onderwerp
            if term_lower in personen.lower() or term_lower in onderwerp.lower():
                score += 15000
            # Match in Inhoud & Cijfers
            if term_lower in inhoud.lower():
                # Geef extra punten als het een jaartal/datum betreft
                if re.search(r'\b\d{4}\b', term_lower):
                    score += 12000
                else:
                    score += 8000

        # 2. Generieke begrippen (Middelgrote weging: 2.000 - 5.000 punten)
        for term in analysis.get('generieke_begrippen', []):
            term_lower = term.lower()
            if not term_lower:
                continue
            if term_lower in onderwerp.lower():
                score += 5000
            if term_lower in inhoud.lower():
                score += 3000

        # 3. Automatische Synoniemen & Documenttypes (bijv. Staatsblad/Balans) (Hoge weging: 6.000 - 10.000 punten)
        for term in analysis.get('synoniemen_documenttypes', []):
            term_lower = term.lower()
            if not term_lower:
                continue
            if term_lower in bestandsnaam.lower():
                score += 10000
            if term_lower in onderwerp.lower() or term_lower in inhoud.lower():
                score += 7000

        return score

    def search_archive(self, user_query: str, top_n_dossiers: int = 20) -> Tuple[pd.DataFrame, Dict[str, Any]]:
        """
        Voert de volledige zoek- en scoringsprocedure uit en retourneert de top geselecteerde dossiers.
        """
        # Stap 1: Analyseer vraag met Gemini
        query_analysis = self.analyze_query_with_gemini(user_query)

        # Stap 2: Bereken score voor elke rij in de Google Sheet / Excel
        df_scored = self.df.copy()
        df_scored['score'] = df_scored.apply(lambda row: self._calculate_score(row, query_analysis), axis=1)

        # Stap 3: Sorteer op score (hoogste eerst)
        df_results = df_scored[df_scored['score'] > 0].sort_values(by='score', ascending=False)

        # Stap 4: Selecteer de top dossiers
        top_dossiers = df_results.head(top_n_dossiers)

        return top_dossiers, query_analysis


# ==========================================
# VOORBEELD VAN GEBRUIK EN TESTEN
# ==========================================
if __name__ == "__main__":
    # Vervang dit door het daadwerkelijke pad naar jouw Excel/Google Sheet inventaris
    EXCEL_FILE = "archief_inventaris.xlsx"

    if os.path.exists(EXCEL_FILE):
        retriever = SmartArchiveRetriever(EXCEL_FILE)
        vraag = "hoe was de financiële toestand van de firma radio belge de construction tussen 1934 en 1940?"
        
        results, analysis = retriever.search_archive(vraag, top_n_dossiers=20)

        print("\n--- AI QUERY ANALYSE ---")
        print(json.dumps(analysis, indent=2, ensure_ascii=False))

        print(f"\n--- TOP {len(results)} GESELECTEERDE ARCHIEFDOCUMENTEN ---")
        for idx, row in results.iterrows():
            print(f"Score: {row['score']} | Bestand: {row.get('Bestandsnaam', 'N/A')} | Onderwerp: {row.get('Onderwerp (NL)', 'N/A')[:60]}")
