"""Fail-closed external market-data entitlement registry.

These entries are deliberately not collectors. A verified, documented free API
and the account entitlement must be recorded before a job may be enabled.
"""

SOURCES = {
    "ES": {"status": "LS_DELAYED_ENTITLEMENT_UNVERIFIED", "realtime": False, "enabled": False,
           "reason": "LS o3121/o3105 code is ready; account-specific no-fee delayed entitlement and contract symbol are unverified"},
    "NQ": {"status": "LS_DELAYED_ENTITLEMENT_UNVERIFIED", "realtime": False, "enabled": False,
           "reason": "LS o3121/o3105 code is ready; account-specific no-fee delayed entitlement and contract symbol are unverified"},
    "NASDAQ_COMPOSITE": {"status": "FREE_API_UNVERIFIED", "enabled": False,
                         "reason": "official symbol, automated access terms and zero fee are unverified"},
    "SP500_CASH": {"status": "FREE_API_UNVERIFIED", "enabled": False,
                    "reason": "official symbol, automated access terms and zero fee are unverified"},
    "KOSPI200_FOREIGN_INTRADAY": {"status": "LS_ENTITLEMENT_UNVERIFIED", "enabled": False,
                                 "reason": "LS DBT DFK2I/NFK2I code is ready; account-specific no-fee right and live payload are unverified"},
    "KOSPI200_FOREIGN_FINAL": {"status": "KRX_OFFICIAL_API_UNAVAILABLE", "enabled": False,
                              "reason": "documented KRX Open API investor-specific futures buy/sell endpoint not identified"},
    "KOSPI200_NIGHT_FUTURES": {"status": "LS_ENTITLEMENT_UNVERIFIED", "enabled": False,
                               "reason": "LS DBT NFK2I investor flow is coded; live contract quotes and free entitlement remain unverified"},
}


def source_status() -> dict[str, dict]:
    return {key: dict(value) for key, value in SOURCES.items()}
