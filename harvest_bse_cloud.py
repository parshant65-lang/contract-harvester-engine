#!/usr/bin/env python3
"""
BSE Historical & Multi-Exchange Contract Harvester
Captures contract awards, order receipts, and LOAs from BSE corporate filings.
Runs on GitHub Actions Cloud Runners.
"""
import os
import sys
import json
import gzip
import time
import re
import xml.etree.ElementTree as ET
import subprocess
import requests
from datetime import datetime, timezone
from contract_parser import parse_order_value, detect_client_sector, extract_client_name
from harvest_contracts import (
    get_auth_remote,
    safe_git,
    VAULT_REPO,
    GITHUB_USER
)

POSITIVE_KEYWORDS = [
    "order", "contract", "awarded", "bagged", "tender", "letter of award", 
    "loa", "work order", "supply order", "lowest bidder", "l1 bidder"
]

NEGATIVE_KEYWORDS = [
    "court order", "penalty", "gst", "tax demand", "adjudication", 
    "loss of share", "resignation", "board meeting", "financial results"
]

def fetch_bse_feeds():
    """Fetches BSE announcement feeds via RSS and public disclosure endpoints."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/xml, text/xml, */*"
    }
    
    feed_urls = [
        "https://news.google.com/rss/search?q=site:bseindia.com+order+contract+awarded+crore&hl=en-IN&gl=IN&ceid=IN:en",
        "https://news.google.com/rss/search?q=site:bseindia.com+work+order+bagged&hl=en-IN&gl=IN&ceid=IN:en"
    ]
    
    items = []
    for u in feed_urls:
        try:
            r = requests.get(u, headers=headers, timeout=20)
            if r.status_code == 200:
                root = ET.fromstring(r.text)
                for item in root.findall(".//item"):
                    title = item.find("title").text if item.find("title") is not None else ""
                    link = item.find("link").text if item.find("link") is not None else ""
                    pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    desc = item.find("description").text if item.find("description") is not None else ""
                    
                    combined = f"{title} {desc}".lower()
                    if any(pos in combined for pos in POSITIVE_KEYWORDS) and not any(neg in combined for neg in NEGATIVE_KEYWORDS):
                        items.append({
                            "title": title,
                            "link": link,
                            "pub_date": pub_date,
                            "desc": desc
                        })
            time.sleep(1)
        except Exception as e:
            print(f"[WARN] Error fetching BSE feed {u}: {e}")
            
    return items

def main():
    pat = os.environ.get("VAULT_PAT")
    if not pat:
        try:
            pat = subprocess.check_output(["gh", "auth", "token"]).decode().strip()
        except Exception:
            pass

    if not pat:
        print("[ERROR] Neither VAULT_PAT nor gh auth token found!")
        sys.exit(1)

    vault_dir = os.path.abspath("vault_work_contracts")
    remote_url = get_auth_remote(pat)

    if not os.path.exists(vault_dir):
        print(f"[INIT] Cloning {VAULT_REPO}...")
        safe_git(["git", "clone", "--depth", "1", remote_url, vault_dir], cwd=".")
    else:
        print(f"[INIT] Pulling latest {VAULT_REPO}...")
        safe_git(["git", "pull", "--rebase"], cwd=vault_dir)

    safe_git(["git", "config", "user.name", "ContractHarvesterBot"], cwd=vault_dir)
    safe_git(["git", "config", "user.email", "bot@contract-intelligence.internal"], cwd=vault_dir)

    seen_ids_path = os.path.join(vault_dir, "seen_ids.json")
    seen_ids = set()
    if os.path.exists(seen_ids_path):
        try:
            with open(seen_ids_path, "r", encoding="utf-8") as f:
                seen_ids = set(json.load(f))
        except Exception:
            seen_ids = set()

    ckpt_path = os.path.join(vault_dir, "checkpoint.json")
    if os.path.exists(ckpt_path):
        with open(ckpt_path, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
    else:
        ckpt = {"vault": VAULT_REPO, "total_contracts_captured": 0, "total_order_value_inr_cr": 0.0}

    print("🚀 Fetching BSE Corporate Announcements & Contracts...")
    feed_items = fetch_bse_feeds()
    print(f"  -> Discovered {len(feed_items)} raw candidate announcements from BSE.")

    bse_contracts = []
    bse_val = 0.0

    for item in feed_items:
        # Generate hash ID
        cid = "BSE_" + str(abs(hash(item["link"] or item["title"])))
        if cid in seen_ids:
            continue

        full_text = f"{item['title']} | {item['desc']}"
        val = parse_order_value(full_text)
        sec = detect_client_sector(full_text)
        cli = extract_client_name(full_text)

        # Extract ticker/company from title (e.g., "Company Name: Receipt of order...")
        comp_match = re.search(r"^([^:\-]+)[:\-]", item["title"])
        company_name = comp_match.group(1).strip() if comp_match else "BSE Listed Entity"

        record = {
            "id": cid,
            "symbol": "BSE_CORP",
            "company": company_name,
            "announcement_time": item.get("pub_date"),
            "category": "Corporate Order Disclosure",
            "summary": item.get("title"),
            "order_value_inr_cr": val,
            "client_sector": sec,
            "client_name": cli,
            "source_url": item.get("link"),
            "source": "BSE_DISCLOSURE"
        }

        bse_contracts.append(record)
        seen_ids.add(cid)
        if val:
            bse_val += val

    print(f"[HARVEST] Ingesting {len(bse_contracts)} NEW genuine BSE contracts (₹{round(bse_val, 2)} Cr)...")

    if bse_contracts:
        master_path = os.path.join(vault_dir, "contracts_master.jsonl")
        with open(master_path, "a", encoding="utf-8") as f:
            for c in bse_contracts:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

        bse_dir = os.path.join(vault_dir, "data", "bse")
        os.makedirs(bse_dir, exist_ok=True)
        chunk_file = os.path.join(bse_dir, f"bse_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json.gz")
        with gzip.open(chunk_file, "wt", encoding="utf-8") as gz:
            json.dump(bse_contracts, gz, indent=2, ensure_ascii=False)

        ckpt["total_contracts_captured"] = ckpt.get("total_contracts_captured", 0) + len(bse_contracts)
        ckpt["total_order_value_inr_cr"] = round(ckpt.get("total_order_value_inr_cr", 0.0) + bse_val, 2)
        ckpt["bse_ingestion_timestamp"] = datetime.now(timezone.utc).isoformat()

        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(ckpt, f, indent=2)
        with open(seen_ids_path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_ids)), f)

        safe_git(["git", "add", "."], cwd=vault_dir)
        safe_git(["git", "commit", "-m", f"[BSE-HARVEST] +{len(bse_contracts)} BSE Contracts (₹{round(bse_val, 2)} Cr) [skip ci]"], cwd=vault_dir)
        safe_git(["git", "push", "origin", "main"], cwd=vault_dir)
        print("✓ BSE Contracts pushed to vault successfully!")
    else:
        print("[INFO] No new BSE items needed ingestion.")

if __name__ == "__main__":
    main()
