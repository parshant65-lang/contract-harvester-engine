#!/usr/bin/env python3
"""
Deep Multi-Year Historical Harvester for Government & Corporate Contract Intelligence (2018–2025)
Runs on GitHub Actions Cloud Runners with zero local resource usage.
"""
import os
import sys
import json
import gzip
import time
import argparse
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

def generate_monthly_windows(start_year: int, end_year: int):
    windows = []
    # Month days lookup
    days_in_month = {
        1: 31, 2: 28, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31
    }
    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            # Leap year check for Feb
            if month == 2 and (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)):
                max_d = 29
            else:
                max_d = days_in_month[month]
            
            from_d = f"01-{month:02d}-{year}"
            to_d = f"{max_d:02d}-{month:02d}-{year}"
            label = f"{year}-{month:02d}"
            windows.append((from_d, to_d, label, year))
    return windows

def fetch_month_data(from_date, to_date):
    url = f"https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={from_date}&to_date={to_date}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
    }
    for attempt in range(5):
        try:
            r = requests.get(url, headers=headers, timeout=25)
            if r.status_code == 200:
                return r.json()
            time.sleep(2 * (attempt + 1))
        except Exception as e:
            time.sleep(2 * (attempt + 1))
    return []

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-year", type=int, default=2018, help="Start Year (e.g. 2018)")
    parser.add_argument("--end-year", type=int, default=2025, help="End Year (e.g. 2025)")
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

    windows = generate_monthly_windows(args.start_year, args.end_year)
    print(f"🚀 Launching Deep Multi-Year Backfill ({args.start_year} to {args.end_year}) across {len(windows)} months...")

    total_added = 0
    total_val = 0.0
    batch_count = 0

    for from_d, to_d, label, year in windows:
        raw_items = fetch_month_data(from_d, to_d)
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

        if month_contracts:
            total_added += len(month_contracts)
            total_val += month_val
            batch_count += len(month_contracts)

            # 1. Append to Master JSONL
            master_path = os.path.join(vault_dir, "contracts_master.jsonl")
            with open(master_path, "a", encoding="utf-8") as f:
                for c in month_contracts:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")

            # 2. Save compressed monthly chunk in data/historical/YYYY/
            hist_dir = os.path.join(vault_dir, "data", "historical", str(year))
            os.makedirs(hist_dir, exist_ok=True)
            chunk_file = os.path.join(hist_dir, f"contracts_{label}.json.gz")
            with gzip.open(chunk_file, "wt", encoding="utf-8") as gz:
                json.dump(month_contracts, gz, indent=2, ensure_ascii=False)

            print(f"  [{label}] +{len(month_contracts)} contracts (₹{round(month_val, 2)} Cr) | Total so far: {total_added}")

        # Periodic intermediate commit every ~1,000 contracts to keep git lightweight
        if batch_count >= 1000:
            print(f"[INTERMEDIATE COMMIT] Pushing {batch_count} contracts to vault...")
            ckpt["total_contracts_captured"] = ckpt.get("total_contracts_captured", 0) + batch_count
            ckpt["total_order_value_inr_cr"] = round(ckpt.get("total_order_value_inr_cr", 0.0) + month_val, 2)
            with open(ckpt_path, "w", encoding="utf-8") as f:
                json.dump(ckpt, f, indent=2)
            with open(seen_ids_path, "w", encoding="utf-8") as f:
                json.dump(sorted(list(seen_ids)), f)

            safe_git(["git", "add", "."], cwd=vault_dir)
            safe_git(["git", "commit", "-m", f"[DEEP-BACKFILL] Ingest +{batch_count} contracts [skip ci]"], cwd=vault_dir)
            safe_git(["git", "push", "origin", "main"], cwd=vault_dir)
            batch_count = 0

        time.sleep(1.5)

    # Final commit
    if batch_count > 0 or total_added > 0:
        ckpt["total_contracts_captured"] = ckpt.get("total_contracts_captured", 0) + batch_count
        ckpt["total_order_value_inr_cr"] = round(ckpt.get("total_order_value_inr_cr", 0.0) + total_val, 2)
        ckpt["multi_year_span"] = f"{args.start_year} to {args.end_year}"
        ckpt["last_deep_backfill"] = datetime.now(timezone.utc).isoformat()

        with open(ckpt_path, "w", encoding="utf-8") as f:
            json.dump(ckpt, f, indent=2)
        with open(seen_ids_path, "w", encoding="utf-8") as f:
            json.dump(sorted(list(seen_ids)), f)

        safe_git(["git", "add", "."], cwd=vault_dir)
        safe_git(["git", "commit", "-m", f"[FINAL-BACKFILL] Total {total_added} Deep Historical Contracts (₹{round(total_val, 2)} Cr) [skip ci]"], cwd=vault_dir)
        safe_git(["git", "push", "origin", "main"], cwd=vault_dir)
        print(f"✓ All Deep Historical Contracts safely stored in {VAULT_REPO}!")

if __name__ == "__main__":
    main()
