"""Deterministic synthetic banking data generators.

A single ``BankUniverse`` holds the consistent set of customers, accounts and loans so
that transactions reference real accounts. Each generator emits *business columns only*
(matching docs/DATA_MODEL.md §1); lineage metadata is added by the Extraction agent when
the data lands in Bronze.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd
from faker import Faker

# --- domain vocabularies (must align with contracts + DATA_MODEL) -----------------
SEGMENTS = ["retail", "retail", "retail", "sme", "corporate", "private"]
EURO_COUNTRIES = ["DE", "FR", "ES", "IT", "NL", "IE", "PT", "AT", "BE", "FI"]
DEPOSIT_PRODUCTS = ["current", "savings", "deposit"]
PRODUCT_TYPES = DEPOSIT_PRODUCTS + ["loan"]
ACCOUNT_STATUS = ["active", "active", "active", "active", "dormant", "closed"]
TXN_TYPES = ["payment", "transfer", "fee", "interest", "withdrawal", "deposit"]
CHANNELS = ["mobile", "web", "atm", "branch", "card"]
# Most volume is EUR; a sprinkle of FX to exercise EUR-normalization at Silver.
CURRENCIES = ["EUR"] * 19 + ["GBP", "USD", "CHF"]
LOAN_STATUS = ["current", "current", "current", "current", "delinquent", "default"]


# Anchor for day-over-day trends. Identity (customers/accounts/loans) stays stable; only
# balances, loan principals, and the daily transaction stream drift with the date, so KPIs
# show realistic movement while remaining deterministic for a given (seed, date).
ANCHOR_DATE = date(2026, 1, 1)


@dataclass
class GeneratorConfig:
    customer_count: int = 1200
    account_count: int = 2000
    loan_count: int = 600
    txns_per_day: int = 5000
    seed: int = 42
    deposit_daily_growth: float = 0.0015   # ~0.15%/day baseline trend
    loan_daily_growth: float = 0.0008
    daily_noise: float = 0.004             # ±0.4% day-to-day jitter


def _trend_factor(as_of: date, daily_growth: float, noise: float, seed: int, salt: int) -> float:
    """Deterministic multiplier: compounding trend since the anchor + bounded daily jitter."""
    days = (as_of - ANCHOR_DATE).days
    r = random.Random(seed * 7919 + as_of.toordinal() + salt)
    jitter = 1 + (r.random() * 2 - 1) * noise
    return (1 + daily_growth) ** days * jitter


class BankUniverse:
    """A consistent, deterministic set of customers/accounts/loans + a daily txn stream."""

    def __init__(self, config: GeneratorConfig | None = None):
        self.config = config or GeneratorConfig()
        self._rng = random.Random(self.config.seed)
        self._faker = Faker("en_IE")
        self._faker.seed_instance(self.config.seed)

        self.customers = self._gen_customers()
        self.accounts = self._gen_accounts()
        self.loans = self._gen_loans()

    # -- dimensions / masters ------------------------------------------------------
    def _gen_customers(self) -> pd.DataFrame:
        rng, fake = self._rng, self._faker
        rows = []
        for i in range(self.config.customer_count):
            onboarded = fake.date_between(start_date="-8y", end_date="-1d")
            rows.append(
                {
                    "customer_id": f"CUST{i:06d}",
                    "full_name": fake.name(),                       # PII
                    "birth_date": fake.date_of_birth(minimum_age=18, maximum_age=90),  # PII
                    "segment": rng.choice(SEGMENTS),
                    "country": rng.choice(EURO_COUNTRIES),
                    "onboarded_date": onboarded,
                }
            )
        return pd.DataFrame(rows)

    def _gen_accounts(self) -> pd.DataFrame:
        rng = self._rng
        customer_ids = self.customers["customer_id"].tolist()
        rows = []
        for i in range(self.config.account_count):
            product = rng.choice(PRODUCT_TYPES)
            is_deposit = product in DEPOSIT_PRODUCTS
            # Deposit accounts hold positive balances; loan accounts track principal elsewhere.
            balance = round(rng.lognormvariate(8.0, 1.1), 2) if is_deposit else round(rng.uniform(-500, 0), 2)
            rows.append(
                {
                    "account_id": f"ACC{i:07d}",
                    "customer_id": rng.choice(customer_ids),
                    "product_type": product,
                    "open_date": self._faker.date_between(start_date="-8y", end_date="-1d"),
                    "status": rng.choice(ACCOUNT_STATUS),
                    "balance": balance,
                    "currency": rng.choice(CURRENCIES),
                }
            )
        return pd.DataFrame(rows)

    def _gen_loans(self) -> pd.DataFrame:
        rng = self._rng
        loan_accounts = self.accounts.loc[self.accounts["product_type"] == "loan", "account_id"].tolist()
        if not loan_accounts:  # ensure at least some loans exist
            loan_accounts = self.accounts["account_id"].sample(
                n=min(self.config.loan_count, len(self.accounts)), random_state=self.config.seed
            ).tolist()
        rows = []
        for i in range(self.config.loan_count):
            status = rng.choice(LOAN_STATUS)
            if status == "current":
                dpd = 0
            elif status == "delinquent":
                dpd = rng.randint(1, 89)
            else:  # default
                dpd = rng.randint(90, 360)
            rows.append(
                {
                    "loan_id": f"LOAN{i:06d}",
                    "account_id": rng.choice(loan_accounts),
                    "principal_outstanding": round(rng.lognormvariate(10.0, 0.8), 2),
                    "interest_rate": round(rng.uniform(2.5, 9.5), 2),
                    "origination_date": self._faker.date_between(start_date="-6y", end_date="-30d"),
                    "days_past_due": dpd,
                    "status": status,
                }
            )
        return pd.DataFrame(rows)

    # -- snapshots / facts ---------------------------------------------------------
    def accounts_snapshot(self, as_of: date) -> pd.DataFrame:
        df = self.accounts.copy()
        factor = _trend_factor(as_of, self.config.deposit_daily_growth, self.config.daily_noise,
                               self.config.seed, salt=1)
        df["balance"] = (df["balance"] * factor).round(2)   # deposits trend day-over-day
        df["snapshot_date"] = as_of
        return df

    def loans_snapshot(self, as_of: date) -> pd.DataFrame:
        df = self.loans.copy()
        factor = _trend_factor(as_of, self.config.loan_daily_growth, self.config.daily_noise,
                               self.config.seed, salt=2)
        df["principal_outstanding"] = (df["principal_outstanding"] * factor).round(2)
        df["snapshot_date"] = as_of
        return df

    def transactions_for_day(self, as_of: date, rows: int | None = None) -> pd.DataFrame:
        """One day's posting stream, referencing real accounts. Reseeded per day so the
        accounts/amounts/volume vary by date (deterministically)."""
        rng = random.Random(self.config.seed * 100003 + as_of.toordinal())
        if rows is not None:
            n = rows
        else:
            # daily volume varies ±8% around the base
            n = int(self.config.txns_per_day * (0.92 + 0.16 * rng.random()))
        # Bias activity toward active deposit accounts.
        active = self.accounts.loc[self.accounts["status"] == "active", "account_id"].tolist()
        pool = active or self.accounts["account_id"].tolist()
        out = []
        for i in range(n):
            txn_type = rng.choice(TXN_TYPES)
            amount = self._amount_for(txn_type, rng)
            posting_ts = datetime.combine(as_of, time(0)) + timedelta(seconds=rng.randint(0, 86399))
            out.append(
                {
                    "txn_id": f"TXN{as_of:%Y%m%d}{i:07d}",
                    "account_id": rng.choice(pool),
                    "posting_ts": posting_ts,
                    "amount": amount,
                    "currency": rng.choice(CURRENCIES),
                    "txn_type": txn_type,
                    "channel": rng.choice(CHANNELS),
                    "counterparty": self._faker.company(),          # PII -> tokenized at Silver
                }
            )
        return pd.DataFrame(out)

    def _amount_for(self, txn_type: str, rng: random.Random) -> float:
        """Signed amount: credits positive, debits negative (matches Silver convention)."""
        mag = round(rng.lognormvariate(4.0, 1.0), 2)
        if txn_type in ("deposit", "interest"):
            return mag
        if txn_type == "fee":
            return -round(rng.uniform(1, 35), 2)
        if txn_type in ("withdrawal", "payment"):
            return -mag
        return mag if rng.random() < 0.5 else -mag  # transfer: either direction


def reference_tables() -> dict[str, pd.DataFrame]:
    """Small lookup/reference tables, fully refreshed each run."""
    products = pd.DataFrame(
        [
            {"product_type": "current", "product_name": "Current Account"},
            {"product_type": "savings", "product_name": "Savings Account"},
            {"product_type": "deposit", "product_name": "Term Deposit"},
            {"product_type": "loan", "product_name": "Loan Facility"},
        ]
    )
    branches = pd.DataFrame(
        [{"branch_id": f"BR{i:03d}", "city": c} for i, c in enumerate(
            ["Dublin", "Cork", "Galway", "Limerick", "Frankfurt", "Paris", "Madrid", "Milan"], start=1
        )]
    )
    channels = pd.DataFrame(
        [{"channel": ch, "is_digital": ch in ("mobile", "web")} for ch in CHANNELS]
    )
    return {"reference_products": products, "reference_branches": branches, "reference_channels": channels}
