"""
Rates API routes — combines card and detail sub-routers.

Card endpoints power the morning-briefing page.
Detail endpoints power the workspace drilldown.
"""

from fastapi import APIRouter

from api.routes.rates.cards import router as cards_router
from api.routes.rates.detail import router as detail_router

router = APIRouter()
router.include_router(cards_router)
router.include_router(detail_router)
