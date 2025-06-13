# market_data_agent/ingestion/kite_client.py

import os
from datetime import datetime
from typing import List, Union

from kiteconnect import KiteConnect
from dotenv import load_dotenv, find_dotenv
from market_data_agent.auth.refresh_token import refresh_kite_access_token

class KiteDataClient:
    """
    Encapsulates:
      - Token refresh (via Selenium+TOTP)
      - KiteConnect session management
      - Instrument token lookup
      - Historical & live data fetch
    """
    def __init__(self):
        # Step 1: Refresh token first
        refresh_kite_access_token()

        # Step 2: Reload the .env AFTER refresh
        load_dotenv(find_dotenv(raise_error_if_not_found=True), override=True)

        # Step 3: Now safely fetch credentials and store them
        self._api_key = os.getenv("KITE_API_KEY")
        self._access_token = os.getenv("KITE_ACCESS_TOKEN")

        self._kite = KiteConnect(api_key=self._api_key)
        self._kite.set_access_token(self._access_token)

        self._instrument_map = self._load_instruments("NSE")

    @property
    def api_key(self) -> str:
        """Exposes the API key for use by KiteTicker."""
        return self._api_key

    @property
    def access_token(self) -> str:
        """Exposes the access token for use by KiteTicker."""
        return self._access_token

    def _load_instruments(self, exchange: str) -> dict[str,int]:
        """
        Fetches and caches all instruments on given exchange.
        """
        insts = self._kite.instruments(exchange)
        return { row["tradingsymbol"]: row["instrument_token"] for row in insts }

    def get_instrument_token(self, symbol: str) -> int:
        """
        Returns the instrument_token required by Kite for any symbol.
        Raises if the symbol is unknown.
        """
        sym = symbol.replace(".NS","")  # normalize if needed
        try:
            return self._instrument_map[sym]
        except KeyError:
            raise ValueError(f"Unknown symbol: {symbol}")

    def fetch_ohlcv(
        self,
        symbols: Union[str,List[str]],
        from_date: Union[str,datetime],
        to_date:   Union[str,datetime],
        interval: str = "day"
    ) -> dict[str, List[dict]]:
        """
        Fetches OHLCV bars for one or more symbols,
        over any date range and supported interval (e.g. "day","5minute","minute").
        Returns a dict: { symbol: [ {date, open, high,low,close,volume}, ... ] }
        """
        if isinstance(symbols, str):
            symbols = [symbols]

        out = {}
        for sym in symbols:
            token = self.get_instrument_token(sym)
            df = self._kite.historical_data(
                instrument_token=token,
                from_date=self._parse_date(from_date),
                to_date=self._parse_date(to_date),
                interval=interval
            )
            out[sym] = df
        return out

    def fetch_ltp(self, symbols: Union[str,List[str]]) -> dict[str,float]:
        """
        Fetches the current Last Traded Price (LTP) for one or more symbols.
        Uses KiteConnect.ltp under the hood.
        """
        if isinstance(symbols, str):
            symbols = [symbols]
        # Kite expects "EXCHANGE:SYMBOL" keys
        keys = [f"NSE:{sym.replace('.NS','')}" for sym in symbols]
        data = self._kite.ltp(keys)
        return { sym: data[f"NSE:{sym.replace('.NS','')}"]["last_price"] for sym in symbols }

    @staticmethod
    def _parse_date(d: Union[str,datetime]) -> str:
        if isinstance(d, datetime):
            return d.strftime("%Y-%m-%d %H:%M:%S")
        return d  # assume already a correctly formatted string