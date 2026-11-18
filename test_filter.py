import json

NEGATIVE_CATEGORIES = [
    "analysts/institutional investor", "con. call", "conference call", 
    "financial results", "investor presentation", "shareholders meeting", 
    "newspaper publication", "postal ballot", "general meeting", 
    "credit rating", "dividend"
]

NEGATIVE_KEYWORDS = [
    "loss of share", "resignation", "appointment of", "trading window",
    "board meeting", "compliance certificate", "penalty", "gst", 
    "tax demand", "assessment order", "adjudication", "court order", 
    "nclt", "order intake", "order backlog", "order book stood at"
]

def is_strict_contract(c):
    cat = (c.get("category") or "").lower()
    if any(nc in cat for nc in NEGATIVE_CATEGORIES):
        return False
    
    desc = (c.get("category") or "").strip()
    summary = (c.get("summary") or "").lower()
    voice = (c.get("voice_summary") or "").lower()
    combined = f"{desc.lower()} {summary} {voice}"
    
    if any(neg in combined for neg in NEGATIVE_KEYWORDS):
        return False
        
    return True

with open("/home/parshant_01/Desktop/contract_harvester_engine/vault_work_contracts/contracts_master.jsonl", "r", encoding="utf-8") as f:
    contracts = [json.loads(line) for line in f]

filtered = [c for c in contracts if is_strict_contract(c)]
print("Total before filter:", len(contracts))
print("Total after strict contract filter:", len(filtered))
print("Sample retained contracts:")
for c in filtered[:5]:
    print(" -", c.get("symbol"), "|", c.get("category"), "| Value:", c.get("order_value_inr_cr"), "Cr |", c.get("summary")[:80])
