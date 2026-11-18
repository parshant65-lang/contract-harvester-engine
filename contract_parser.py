#!/usr/bin/env python3
"""
Contract Intelligence NLP & Regex Parser
Extracts order values, client entities, and contract scope from corporate filings.
"""
import re
from typing import Dict, Any, Optional

CLIENT_SECTOR_PATTERNS = [
    (r"(?i)\b(indian\s+railways|railways?|irctc|rvnl|ircon|cris|rail\s*tel)\b", "Railways & Transit"),
    (r"(?i)\b(defence|ministry\s+of\s+defence|mod|army|navy|air\s*force|drdo|hal|bel|mazagon)\b", "Defence & Aerospace"),
    (r"(?i)\b(nhai|highways?|road\s+transport|road\s+development|morth|state\s+highways?|pwd|cpwd)\b", "Roads & Highways"),
    (r"(?i)\b(metro\s+rail|dmrc|mmrda|bmrc|cmrl|kmrl)\b", "Metro Rail Infrastructure"),
    (r"(?i)\b(power\s*grid|pgcil|ntpc|bhel|nhpc|sjvn|seci|ireda|discom|t&d)\b", "Power & Renewable Energy"),
    (r"(?i)\b(ongc|iocl|bpcl|hpcl|gail|oil\s+india|petrochemical)\b", "Oil & Gas / Energy"),
    (r"(?i)\b(isro|space|telecom|bsnl|dot|trai)\b", "Telecom & Technology"),
    (r"(?i)\b(jal\s+shakti|water\s+resources?|smart\s+cit(y|ies)|urban\s+development)\b", "Water & Urban Infrastructure"),
    (r"(?i)\b(larson\s*&\s*toubro|l&t|tata|reliance|adani|posh\s+india)\b", "Private Corporate EPC"),
]

def parse_order_value(text: str) -> Optional[float]:
    """
    Extracts total order/contract value in INR Crores.
    Returns float or None.
    """
    if not text:
        return None
        
    clean_text = text.replace(",", "")
    
    # Pattern 1: ₹ / Rs / INR ... Crore / Cr
    p1 = re.search(r"(?:(?:rs\.?|inr|₹)\s*([\d\.]+)\s*(?:crores?|crs?\.?)\b)", clean_text, re.IGNORECASE)
    if p1:
        try:
            return round(float(p1.group(1)), 2)
        except ValueError:
            pass

    # Pattern 2: Number followed by Crore/Cr with Rs/INR anywhere near
    p2 = re.search(r"\b([\d\.]+)\s*(?:crores?|crs?\.?)\b", clean_text, re.IGNORECASE)
    if p2:
        try:
            val = float(p2.group(1))
            if 0.1 <= val <= 100000.0:  # Sanity range
                return round(val, 2)
        except ValueError:
            pass

    # Pattern 3: Lakhs / Lacs -> convert to Crores (1 Lakh = 0.01 Cr)
    p3 = re.search(r"(?:(?:rs\.?|inr|₹)\s*([\d\.]+)\s*(?:lakhs?|lacs?)\b)", clean_text, re.IGNORECASE)
    if p3:
        try:
            lakhs = float(p3.group(1))
            return round(lakhs / 100.0, 4)
        except ValueError:
            pass

    # Pattern 4: USD / Millions / Billions
    p4 = re.search(r"(?:(?:usd|\$)\s*([\d\.]+)\s*(?:million|mn)\b)", clean_text, re.IGNORECASE)
    if p4:
        try:
            usd_mn = float(p4.group(1))
            return round(usd_mn * 8.4, 2)
        except ValueError:
            pass

    return None

def detect_client_sector(text: str) -> str:
    """Detects the client sector from text context."""
    if not text:
        return "General Corporate / Commercial"
    for pattern, sector in CLIENT_SECTOR_PATTERNS:
        if re.search(pattern, text):
            return sector
    return "General Corporate / Commercial"

def extract_client_name(text: str) -> Optional[str]:
    """Tries to extract the entity awarding the contract."""
    if not text:
        return None
        
    patterns = [
        r"(?i)(?:awarded\s+by|received\s+from|order\s+from|contract\s+from|loa\s+from|\bfrom\b)\s+([A-Z][A-Za-z0-9\s\&\.\,\-]{3,60}?)(?=\s+(?:for|worth|aggregating|towards|to|dated|vide|amounting|\.|$))",
        r"(?i)(?:client|customer|employer)\s*:\s*([A-Za-z0-9\s\&\.\,\-]{3,50})"
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            candidate = m.group(1).strip()
            candidate = re.sub(r"[\,\.\;\:]+$", "", candidate).strip()
            if 2 < len(candidate) < 70 and not re.match(r"^[\d\s\/\-\.]+$", candidate):
                return candidate
    return None

def parse_contract_disclosure(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Parses an announcement record into a structured Contract Intelligence Record.
    """
    symbol = (item.get("symbol") or "").strip()
    company = (item.get("sm_name") or symbol).strip()
    desc = (item.get("desc") or "").strip()
    summary = (item.get("attchmntText") or "").strip()
    voice_summary = (item.get("voiceSummary") or "").strip()
    combined_text = f"{desc} | {summary} | {voice_summary}"
    
    order_value = parse_order_value(combined_text)
    sector = detect_client_sector(combined_text)
    client_name = extract_client_name(combined_text)
    
    return {
        "id": f"NSE_{item.get('seq_id') or item.get('dt')}",
        "symbol": symbol,
        "company": company,
        "isin": item.get("sm_isin"),
        "announcement_time": item.get("an_dt"),
        "sort_date": item.get("sort_date"),
        "category": desc,
        "summary": summary,
        "voice_summary": voice_summary if voice_summary else None,
        "order_value_inr_cr": order_value,
        "client_sector": sector,
        "client_name": client_name,
        "pdf_url": item.get("attchmntFile"),
        "audio_summary_url": item.get("voiceUrl"),
        "source": "NSE_DISCLOSURE"
    }
