#!/usr/bin/env python3
"""
1-Year Historical Backfiller for Government & Corporate Contract Intelligence
Fetches 12 months of corporate announcements from NSE, filters genuine contract awards,
and updates the private vault.
"""
import os
import sys
import json
import gzip
import time
import subprocess
import requests
from datetime import datetime, timezone
from contract_parser import parse_contract_disclosure
from harvest_contracts import (
    is_genuine_contract,
    get_auth_remote,
    safe_git,
    VAULT_REPO,
    GITHUB_USER
)

MONTH_WINDOWS = [
    ("18-11-2025", "30-11-2025", "Nov 2025 Part 2"),
    ("01-12-2025", "31-12-2025", "Dec 2025"),
    ("01-01-2026", "31-01-2026", "Jan 2026"),
    ("01-02-2026", "28-02-2026", "Feb 2026"),
    ("01-03-2026", "31-03-2026", "Mar 2026"),
    ("01-04-2026", "30-04-2026", "Apr 2026"),
    ("01-05-2026", "31-05-2026", "May 2026"),
    ("01-06-2026", "30-06-2026", "Jun 2026"),
    ("01-07-2026", "31-07-2026", "Jul 2026"),
    ("01-08-2026", "31-08-2026", "Aug 2026"),
    ("01-09-2026", "30-09-2026", "Sep 2026"),
    ("01-10-2026", "31-10-2026", "Oct 2026"),
    ("01-11-2026", "18-11-2026", "Nov 2026 Part 1")
]

def fetch_month_announcements(from_date, to_date):
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={from_date}&to_date={to_date}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    }
    for attempt in range(5):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()
            time.sleep(3 * (attempt + 1))
        except Exception as e:
            print(f"[RETRY] Error fetching {from_date} to {to_date}: {e}")
            time.sleep(3 * (attempt + 1))
    return []

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

    # Load seen IDs
    seen_ids_path = os.path.join(vault_dir, "seen_ids.json")
    seen_ids = set()
    if os.path.exists(seen_ids_path):
        try:
            with open(seen_ids_path, "r", encoding="utf-8") as f:
                seen_ids = set(json.load(f))
        except Exception:
            seen_ids = set()

    # Load checkpoint
    ckpt_path = os.path.join(vault_dir, "checkpoint.json")
    if os.path.exists(ckpt_path):
        with open(ckpt_path, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
    else:
        ckpt = {"vault": VAULT_REPO, "total_contracts_captured": 0, "total_order_value_inr_cr": 0.0}

    total_added_all = 0
    total_val_all = 0.0

    print("==========================================================")
    print("🚀 STARTING 1-YEAR HISTORICAL CONTRACT BACKFILL (12 MONTHS)")
    print("==========================================================")

    for from_date, to_date, label in MONTH_WINDOWS:
        print(f"\n[WINDOW] {label}: {from_date} to {to_date}...")
        raw_items = fetch_month_announcements(from_date, to_date)
        print(f"  -> Fetched {len(raw_items)} filings from NSE.")

        month_contracts = []
        month_val = 0.0

        for item in raw_items:
            seq_id = str(item.get("seq_id") or item.get("dt"))
            cid = f"NSE_{seq_id}"

            if cid in seen_ids:
                continue

            if is_genuine_contract(item):
                parsed = parse_contract_disclosure(item)
                month_contracts.append(parsed)
                seen_ids.add(cid)
                if parsed.get("order_value_inr_cr"):
                    month_val += parsed["order_value_inr_cr"]

        print(f"  -> Discovered {len(month_contracts)} NEW genuine contracts (Value: ₹{round(month_val, 2)} Cr)")
        total_added_all += len(month_contracts)
        total_val_all += month_val

        # Save into monthly archive if any contracts
        if month_contracts:
            # 1. Append to master JSONL
            master_path = os.path.join(vault_dir, "contracts_master.jsonl")
            with open(master_path, "a", encoding="utf-8") as f:
                for c in month_contracts:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")

            # 2. Save compressed monthly chunk
            month_tag = label.replace(" ", "_")
            hist_dir = os.path.join(vault_dir, "data", "historical")
            os.makedirs(hist_dir, exist_ok=True)
            chunk_file = os.path.join(hist_dir, f"contracts_{month_tag}.json.gz")
            with gzip.open(chunk_file, "wt", encoding="utf-8") as gz:
                json.dump(month_contracts, gz, indent=2, ensure_ascii=False)

        # Politeness pause
        time.sleep(2)

    print("\n==========================================================")
    print(f"🎉 1-YEAR BACKFILL COMPLETE: +{total_added_all} Contracts Added!")
    print(f"💰 New Contract Value Captured: ₹{round(total_val_all, 2)} Crore")
    print("==========================================================")

    if total_added_all > 0:
        # Update seen_ids
        with open(seen_ids_path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_ids)), f)

        # Update checkpoint
        ckpt["total_contracts_captured"] = ckpt.get("total_contracts_captured", 0) + total_added_all
        ckpt["total_order_value_inr_cr"] = round(ckpt.get("total_order_value_inr_cr", 0.0) + total_val_all, 2)
        ckpt["last_backfill_timestamp"] = datetime.now(timezone.utc).isoformat()
        ckpt["historical_span"] = "18-Nov-2025 to 18-Nov-2026 (1 Full Year)"
        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(ckpt, f, indent=2)

        # Commit and push
        print(f"[VAULT] Pushing complete 1-Year dataset to {VAULT_REPO}...")
        safe_git(["git", "add", "."], cwd=vault_dir)
        commit_msg = f"[BACKFILL] +{total_added_all} 1-Year Contracts (Total: {ckpt['total_contracts_captured']} | ₹{ckpt['total_order_value_inr_cr']} Cr) [skip ci]"
        safe_git(["git", "commit", "-m", commit_msg], cwd=vault_dir)
        safe_git(["git", "push", "origin", "main"], cwd=vault_dir)
        print(f"✓ All 1-Year Historical Contracts Safely Stored in {VAULT_REPO}!")

if __name__ == "__main__":
    main()
