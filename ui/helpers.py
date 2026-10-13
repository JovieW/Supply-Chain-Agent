"""
UI helper functions.
Currently minimal - reserved for future utilities.
"""

def format_currency(amount: float) -> str:
    """Format number as currency."""
    return f"${amount:,.2f}"


def format_percentage(value: float) -> str:
    """Format decimal as percentage."""
    return f"{value:.1%}"


def truncate_text(text: str, max_length: int = 100) -> str:
    """Truncate long text with ellipsis."""
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."
