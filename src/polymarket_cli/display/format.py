"""Number and string formatting helpers."""


def fmt_volume(usd: float) -> str:
    """Format a USD volume: $1.2M, $340K, $88."""
    if usd >= 1_000_000:
        return f"${usd / 1_000_000:.1f}M"
    if usd >= 1_000:
        return f"${usd / 1_000:.1f}K"
    return f"${usd:.0f}"


def fmt_price(price: float) -> str:
    """Format a probability as cents: 94¢, <1¢, 100¢."""
    cents = price * 100
    if cents < 1:
        return "<1¢"
    if cents >= 99.5:
        return "100¢"
    return f"{cents:.0f}¢"


def fmt_delta(delta: float) -> tuple[str, str]:
    """Return (text, style) for a price delta.

    delta is in price units (0.0–1.0). Displayed as cents.
    Returns e.g. ("▲0.4", "green") or ("▼0.1", "red") or ("—", "dim").
    """
    cents = delta * 100
    if abs(cents) < 0.05:
        return "—", "dim"
    arrow = "▲" if cents > 0 else "▼"
    style = "green" if cents > 0 else "red"
    return f"{arrow}{abs(cents):.1f}", style


def fmt_volume_delta(usd: float) -> tuple[str, str]:
    """Return (text, style) for a 24hr volume change."""
    if usd == 0:
        return "—", "dim"
    sign = "+" if usd > 0 else "-"
    style = "green" if usd > 0 else "red"
    return f"{sign}{fmt_volume(abs(usd))}", style


def truncate(text: str, width: int) -> str:
    """Truncate text to width with ellipsis."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"
