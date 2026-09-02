<div align="center">

# QUANT_NQ

### An algorithmic trading platform built on a kernel that knows nothing.

![core](https://img.shields.io/badge/core-1.27.0-1f6feb)
![python](https://img.shields.io/badge/python-3.12%2B-3776ab?logo=python&logoColor=white)
![async](https://img.shields.io/badge/runtime-asyncio-2ea043)
![sealed](https://img.shields.io/badge/core-SHA--256%20sealed-8957e5)
![license](https://img.shields.io/badge/license-proprietary-6e7681)
[![Telegram](https://img.shields.io/badge/Telegram-Join%20us-26A5E4?logo=telegram&logoColor=white)](https://t.me/quantnq)

### 🚀 Join us on Telegram → **[t.me/quantnq](https://t.me/quantnq)**

</div>

---

## The idea

QUANT_NQ is built on one strict principle: **the core knows nothing.**
It doesn't know the name or number of a single atom. All behavior lives in
independent **atoms**; all communication flows through **one event bus**.
Drop a folder with `atom.py` + `manifest.yaml` — the atom runs. Delete it —
it's gone. Without touching a single line of the core.

> Every number below is read from the code itself — the contracts, the
> bootloader, the bus, and the manifests — not from design docs.

## By the numbers

| | |
|---|---|
| Forex atoms | **233** under `atoms/` |
| Crypto atoms | **76** under `atoms_crypto/` — isolated, advisory |
| Sealed core files | **23** (`core/CORE.lock`) |
| Core version | **1.27.0** — sealed `2026-08-27` |
| Governance checks | **71** under `governance/checks/` |
| Python | **≥ 3.12**, fully `asyncio` |

## Architecture

- **🧠 Zero-knowledge core** — the bootloader only orchestrates: *Scan → Load
  Manifest → Validate → Register → Resolve Dependencies → Initialize → Start.*
  No atom name or number is hard-coded anywhere.
- **🔒 Sealed core** — 23 files, each SHA-256 stamped under a single root digest;
  any manual edit breaks the seal and is detected.
- **🧩 Strict atom isolation** — no atom calls another directly; publish/subscribe only.
- **🔀 One event bus** — guards against duplicate orders; independent state per **(account × symbol)**.
- **⚖️ Governance above the core** — 71 checks govern safety, execution, and release.
- **🪙 Two isolated markets** — forex and crypto on separate buses, shared
  infrastructure and governance, never mixed.

## Structure

```text
core/           sealed kernel (23 files) — knows no atom
atoms/          forex atoms (233)
atoms_crypto/   crypto atoms (76) — isolated, advisory
transport/      ownership bus (distribute ownership, not copies)
governance/     checks (71), dashboards, launchers
shared/         shared contracts & components
config/         per-market core config
scripts/        unified launchers (forex · crypto · governance)
```

## Quick start

```bash
python scripts/launch_unified.py
```

Unified dashboard at `http://127.0.0.1:8090` — toggle **Forex ⇄ Crypto** with one button.

## License

**Proprietary — all rights reserved.** No use, copy, modification, or
distribution without the owner's prior written consent. See [LICENSE](LICENSE).

<div align="center">

### 📢 Signals & updates on Telegram → **[t.me/quantnq](https://t.me/quantnq)**

<sub>Built from the code · Sealed by the code · Governed by the code.</sub>
</div>
