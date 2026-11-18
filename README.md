# 🏛️ Government & Corporate Contract Intelligence Harvester Engine

Autonomous 24/7 cloud pipeline capturing real-time and historical **Tender Wins, Government Contracts, Letters of Award (LOA), and Corporate Order Receipts** across Indian listed companies and public procurement channels.

## 📡 Live Pipeline Architecture
- **Engine Repository (Public)**: `parshant65-lang/contract-harvester-engine` (Runs GitHub Actions runners)
- **Data Vault (Private)**: `parshant65-lang/contract-intelligence-vault` (Private storage tier)
- **Primary Source**: National Stock Exchange (NSE) & Bombay Stock Exchange (BSE) Regulatory Disclosures under SEBI Regulation 30 (LODR).

## 📊 Extracted Entity Schema
- `symbol`: Stock Ticker (e.g. `RVNL`, `BEL`, `HAL`, `MARKOLINES`, `LT`)
- `company`: Official registered company name
- `order_value_inr_cr`: Numeric value in INR Crores (regex/NLP parsed)
- `client_sector`: Railways, Defence, Highways/NHAI, Power/Renewables, Metro, Oil & Gas, etc.
- `client_name`: Entity awarding the contract
- `pdf_url`: Direct official signed filing document link
- `announcement_time`: Precise timestamp of announcement

## 🚀 Deployment & Triggering
- **Market Hours**: Streams every 15 minutes between 8:30 AM to 6:30 PM IST (Mon–Fri).
- **Off-Market**: Runs hourly health checks.
- **Manual Backfill**: Via `workflow_dispatch` with custom backfill day ranges.
