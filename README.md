# blockchain_economic_stimulus
*A macroeconomic simulator and real-time digital settlement app*

## 🧭 What It Is
**blockchain_economic_stimulus** is a prototype application that models and simulates a digital fiscal stimulus program — where government-issued wallets distribute funds that circulate locally under controlled leakages and VAT feedback.

The project integrates:
- A **FastAPI backend** handling negotiation, settlement, and monitoring logic.  
- A **React frontend** providing real-time transaction and deal visualization.  
- A **simulation engine** inspired by New Keynesian (NK) and DSGE frameworks.  
- **Docker-ready deployment** for easy testing and demo presentation.

---

## 💡 Why It's Important
- Demonstrates how a **programmable fiscal policy** can be executed on a blockchain-like network (ARC with USDC).  
- Provides **policy insight** into multiplier effects, leakage control, and VAT recapture.  
- Acts as a **proof-of-concept** for transparent, auditable, and geo-fenced stimulus design.  
- Bridges **macroeconomic theory** with **practical digital settlement systems**.

---

## ⚙️ Core Principle
The app follows three design layers:
1. **Negotiation Layer** – agents (payer, vendor, auditor) reach settlement terms.  
2. **Settlement Layer** – simulated ARC–USDC transactions confirm deals.  
3. **Monitoring Layer** – live dashboards visualize flows, multipliers, and VAT recovery.

Underlying principle:
> Tiered circularity and geo-fenced spending maximize local multipliers  
> while maintaining auditability and fiscal feedback.

---

## 🐳 Quick Deploy (Docker)

1. **Clone and enter**
   ```bash
   git clone https://github.com/<your-username>/hackathon_app.git
   cd hackathon_app
   ./deploy.sh
