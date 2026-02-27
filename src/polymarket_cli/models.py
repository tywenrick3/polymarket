from dataclasses import dataclass, field


@dataclass
class Outcome:
    name: str
    price: float          # 0.0 – 1.0
    price_delta: float    # change over 24h (in price units, e.g. 0.04 = +4¢)
    token_id: str = ""


@dataclass
class Market:
    id: str
    question: str
    outcomes: list[Outcome] = field(default_factory=list)
    volume: float = 0.0
    volume_24hr: float = 0.0
    token_ids: list[str] = field(default_factory=list)


@dataclass
class Event:
    id: str
    slug: str
    title: str
    volume: float
    volume_24hr: float
    liquidity: float
    markets: list[Market] = field(default_factory=list)
    end_date: str = ""
    resolution_source: str = ""
