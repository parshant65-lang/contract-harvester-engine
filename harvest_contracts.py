#!/usr/bin/env python3
"""
Autonomous Government & Corporate Contract Intelligence Harvester
Captures tender wins, order receipts, LOAs, and corporate order awards from stock exchange filings.
"""
import os
import sys
import json
import gzip
import time
import re
import argparse
import subprocess
import requests
from datetime import datetime, timedelta, timezone
from contract_parser import parse_contract_disclosure

VAULT_REPO = "contract-intelligence-vault"
GITHUB_USER = "parshant65-lang"

EXACT_POSITIVE_CATEGORIES = [
    "bagging/receiving of orders/contracts",
    "award of order / receipt of order"
]

NEGATIVE_CATEGORIES = [
    "analysts/institutional investor", "con. call", "conference call", 
    "financial results", "investor presentation", "shareholders meeting", 
    "newspaper publication", "postal ballot", "general meeting", 
    "credit rating", "dividend", "price movement", "loss of share",
    "clarification", "insider trading", "corporate insolvency",
    "cirp", "action(s) taken or orders passed"
]

POSITIVE_PHRASES = [
    r"\b(bagged|awarded|bags|secures|secured)\b.*?\b(order|contract|tender|project|mandate|package)\b",
    r"\b(receipt|receiving|receives|received)\b.*?\b(of\s+)?(order|contract|loa|work\s+order|supply\s+order)\b",
    r"\b(letter\s+of\s+award|loa|letter\s+of\s+intent|loi)\b",
    r"\b(l1\s+bidder|lowest\s+bidder)\b",
    r"\b(work\s+order|purchase\s+order|supply\s+order|epc\s+contract|turnkey\s+contract|power\s+purchase\s+agreement)\b"
]

NEGATIVE_PHRASES = [
    r"\b(court\s+order|assessment\s+order|penalty|adjudication\s+order|gst|tax\s+demand|nclt|cirp)\b",
    r"\b(order\s+intake|order\s+backlog|order\s+book\s+stood)\b",
    r"\b(orderly\s+trading|order\s+matching)\b"
]

def get_auth_remote(pat: str) -> str:
    return f"https://x-access-token:{pat}@github.com/{GITHUB_USER}/{VAULT_REPO}.git"

def safe_git(cmd, cwd):
    for attempt in range(3):
        try:
            return subprocess.check_output(cmd, cwd=cwd, stderr=subprocess.STDOUT).decode().strip()
        except subprocess.CalledProcessError as e:
            err = e.output.decode() if e.output else str(e)
            print(f"[GIT-WARN] Attempt {attempt+1} failed: {err}")
            time.sleep(3)
    raise RuntimeError(f"Git command failed: {' '.join(cmd)}")

def is_genuine_contract(item: dict) -> bool:
    cat = (item.get("desc") or "").lower().strip()
    summary = (item.get("attchmntText") or "").lower().strip()
    voice = (item.get("voiceSummary") or "").lower().strip()
    combined = f"{cat} | {summary} | {voice}"

    if any(nc in cat for nc in NEGATIVE_CATEGORIES):
        return False
    if any(re.search(np, combined) for np in NEGATIVE_PHRASES):
        return False

    if any(cat == ep for ep in EXACT_POSITIVE_CATEGORIES):
        return True

    for pp in POSITIVE_PHRASES:
        if re.search(pp, combined):
            return True

    return False

def fetch_exchange_announcements(from_date=None, to_date=None):
    """Fetches corporate announcements from NSE, with optional date filtering."""
    if from_date and to_date:
        url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={from_date}&to_date={to_date}"
        print(f"[API] Querying historical window: {from_date} to {to_date}...")
    else:
        url = "https://www.nseindia.com/api/corporate-announcements?index=equities"
        print("[API] Querying real-time live disclosures...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    }
    
    for attempt in range(5):
        try:
            r = requests.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                data = r.json()
                print(f"[API] Successfully fetched {len(data)} raw announcements from NSE.")
                return data
            else:
                print(f"[API-WARN] HTTP {r.status_code}, retry {attempt+1}/5...")
                time.sleep(3 * (attempt + 1))
        except Exception as e:
            print(f"[NET-WARN] Connection failed: {e}, retry {attempt+1}/5...")
            time.sleep(3 * (attempt + 1))
    return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill-days", type=int, default=0, help="Days of historical announcements to backfill")
    args = parser.parse_args()

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

    if os.path.exists(vault_dir):
        subprocess.run(["rm", "-rf", vault_dir])

    print(f"[INIT] Cloning {VAULT_REPO}...")
    safe_git(["git", "clone", "--depth", "1", remote_url, vault_dir], cwd=".")
    safe_git(["git", "config", "user.name", "ContractHarvesterBot"], cwd=vault_dir)
    safe_git(["git", "config", "user.email", "bot@contract-intelligence.internal"], cwd=vault_dir)

    # Checkpoint
    ckpt_path = os.path.join(vault_dir, "checkpoint.json")
    if os.path.exists(ckpt_path):
        with open(ckpt_path, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
    else:
        ckpt = {
            "vault": VAULT_REPO,
            "total_contracts_captured": 0,
            "total_order_value_inr_cr": 0.0
        }

    seen_ids_path = os.path.join(vault_dir, "seen_ids.json")
    seen_ids = set()
    if os.path.exists(seen_ids_path):
        try:
            with open(seen_ids_path, "r", encoding="utf-8") as f:
                seen_ids = set(json.load(f))
        except Exception:
            seen_ids = set()

    # Determine query mode: Backfill vs Live
    backfill_days = args.backfill_days
    announcements = []
    if backfill_days > 0:
        now = datetime.now()
        start = now - timedelta(days=backfill_days)
        from_str = start.strftime("%d-%m-%Y")
        to_str = now.strftime("%d-%m-%Y")
        announcements = fetch_exchange_announcements(from_date=from_str, to_date=to_str)
    
    # Also fetch real-time live disclosures
    live_items = fetch_exchange_announcements()
    announcements.extend(live_items)

    new_contracts = []
    total_val_cycle = 0.0

    for item in announcements:
        seq_id = str(item.get("seq_id") or item.get("dt"))
        contract_id = f"NSE_{seq_id}"

        if contract_id in seen_ids:
            continue

        if is_genuine_contract(item):
            parsed = parse_contract_disclosure(item)
            new_contracts.append(parsed)
            seen_ids.add(contract_id)
            if parsed.get("order_value_inr_cr"):
                total_val_cycle += parsed["order_value_inr_cr"]

    print(f"[HARVEST] Discovered {len(new_contracts)} GENUINE order/contract awards!")

    if not new_contracts:
        print("[INFO] Vault is already up-to-date with latest filings.")
        sys.exit(0)

    # Ingest into Vault
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_dir = os.path.join(vault_dir, "data", today_str)
    os.makedirs(today_dir, exist_ok=True)

    # 1. Append to Master JSONL
    master_path = os.path.join(vault_dir, "contracts_master.jsonl")
    with open(master_path, "a", encoding="utf-8") as f:
        for c in new_contracts:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")

    # 2. Append to Daily Compressed GZ
    batch_time = datetime.now().strftime("%H-%M-%S")
    chunk_file = os.path.join(today_dir, f"contracts_{batch_time}.json.gz")
    with gzip.open(chunk_file, "wt", encoding="utf-8") as gz:
        json.dump(new_contracts, gz, indent=2, ensure_ascii=False)

    # 3. Update seen_ids (keep last 50,000 IDs)
    if len(seen_ids) > 50000:
        seen_ids = set(list(seen_ids)[-50000:])
    with open(seen_ids_path, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen_ids)), f)

    # 4. Update Checkpoint
    ckpt["total_contracts_captured"] = ckpt.get("total_contracts_captured", 0) + len(new_contracts)
    ckpt["total_order_value_inr_cr"] = round(ckpt.get("total_order_value_inr_cr", 0.0) + total_val_cycle, 2)
    ckpt["last_run_timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(ckpt_path, "w", encoding="utf-8") as f:
        json.dump(ckpt, f, indent=2)

    # Git Commit and Push
    print(f"[VAULT] Committing +{len(new_contracts)} contracts to {VAULT_REPO}...")
    safe_git(["git", "add", "."], cwd=vault_dir)
    commit_msg = f"[HARVEST] +{len(new_contracts)} Verified Contracts (Total: {ckpt['total_contracts_captured']} | ₹{ckpt['total_order_value_inr_cr']} Cr) [skip ci]"
    safe_git(["git", "commit", "-m", commit_msg], cwd=vault_dir)
    safe_git(["git", "push", "origin", "main"], cwd=vault_dir)
    print(f"✓ Push successful! Total in Vault: {ckpt['total_contracts_captured']} contracts (Total Value: ₹{ckpt['total_order_value_inr_cr']} Cr).")

if __name__ == "__main__":
    main()
