"""FX NDF (Non-Deliverable Forward) sub-agent.

NDFs are quoted as OUTRIGHTS (not forward points like deliverable G10/EM
forwards), so they have their own compute path distinct from
fx_agent.forwards. The 6 NDF families currently in DB (Phase B Wave 2
BBG batch + Warehouse seeding 2026-05-25): CCN+ (USDCNY), IRN+ (USDINR),
BCN+ (USDBRL), KWN+ (USDKRW), IHN+ (USDIDR), NTN+ (USDTWD) × 5 tenors
each = 30 outrights. Bid/ask landed in PR #230.

Phase D tools live under fx_agent/ndf/tools/. ADR 0008 (Domain.FX split
into FX_CASH / FX_VOL / FX_NDF) is deferred per G3 — the directory
structure is already aligned with that future split.
"""
