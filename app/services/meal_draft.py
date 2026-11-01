"""What is left of the meal draft: the callback prefix its old buttons still carry on
someone's screen, and the number format everyone shares. The draft itself, its keyboard
and its steps are retired (section 3.2): a meal is written the moment it arrives.
"""

CALLBACK_PREFIX = "meal:"


def num(value, digits: int = 1) -> str:
    """A number without a trailing ".0": 250.0 → "250", 22.24 → "22.2"."""
    text = f"{round(float(value), digits):.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text
