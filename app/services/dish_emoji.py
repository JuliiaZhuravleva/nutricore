"""A dish's emoji, from the words of its name (owner 2026-11-10).

A button cuts a long name, and a picture finds a dish faster than the words do. The
emoji comes from the name alone, so every dish has one at once: the ones saved before
this, and the ones saved by any read, with no column, no backfill and no model call.

The table runs in order and the first stem found wins, so a dish named for what it is
(«картошка фри», «салат с курицей») comes before what it is made of. A stem is a
lowercase prefix of a word: «яблок» finds «яблоко» and «яблочный» too. A name with
none of them gets ``DEFAULT``.
"""

import re
from typing import Optional

DEFAULT = "🍽"

# What stands between a button's dish emoji and the name: a no-break space. With custom
# emoji on, ``custom_emoji`` turns a label's leading «mark + space» into a drawn icon;
# only some foods are drawn, so the list would mix drawn and stock pictures. Kept stock,
# every row's picture is the phone's own.
SEP = "\u00a0"

# (emoji, stems). Dishes first, then drinks and sweets, then what they are made of.
_TABLE = (
    # brands: «Twix … с печеньем» is a chocolate bar, not a biscuit
    ("🍫", ("snickers", "twix", "kinder", "bueno", "m&m", "bounty", "mars")),
    # dishes
    ("🍟", ("картошка фри", "картофель фри", "fries")),
    ("🍕", ("пицц", "pizza")),
    ("🍔", ("бургер", "burger")),
    ("🌯", ("тортиль", "шаурм", "буррито", "ролл", "лаваш", "wrap")),
    ("🌮", ("тако", "taco")),
    ("🥪", ("сэндвич", "бутерброд", "тост", "sandwich")),
    ("🍣", ("суши", "поке", "poke", "sushi")),
    ("🥬", ("салатн", "листь")),  # leaves, not a salad
    ("🥗", ("салат", "оливье", "боул", "bowl", "salad")),
    ("🍲", ("суп", "борщ", "щи", "рагу", "soup")),
    ("🍜", ("лапш", "рамен", "noodle")),
    ("🍝", ("спагет", "паста", "пасту", "пасты", "макарон", "pasta", "spaghetti")),
    ("🥞", ("блин", "блинч", "панкейк", "оладь", "сырник", "pancake")),
    ("🧇", ("вафл", "waffle")),
    ("🥟", ("пельмен", "вареник", "хинкал", "манты", "dumpling")),
    ("🍳", ("яичниц", "омлет", "яйц", "яйко", "egg")),
    ("🥣", ("гранол", "мюсли", "каш", "овсян", "хлопь", "granola", "oat")),
    ("🍿", ("попкорн", "popcorn")),
    ("🥐", ("выпечк", "круассан", "пирож", "пирог", "булоч", "pastry")),
    # drinks
    ("🧋", ("бабл", "тапиок", "bubble")),
    ("☕", ("кофе", "американо", "капучин", "латте", "эспрессо", "coffee")),
    ("🍵", ("чай", "матча", "tea")),
    ("🥛", ("молок", "кефир", "milk")),
    ("🧃", ("сок", "смузи", "juice")),
    # sweets
    ("🍦", ("морожен", "пломбир", "ice cream")),
    ("🍰", ("торт", "чизкейк", "бисквит", "пирожн", "сырок", "десерт", "cake")),
    ("🍪", ("печенье", "печенья", "печеньк", "cookie")),
    ("🍫", ("шоколад", "батончик")),
    ("🍬", ("конфет", "драже", "мармелад", "карамел", "candy", "candies")),
    ("🍯", ("мёд", "мед", "варень", "джем", "honey")),
    ("🍦", ("сливк", "cream")),
    # protein powder, nuts, fish, meat and what stands in for them
    ("🐟", ("лосос", "сёмг", "семг", "рыб", "тунец", "форел", "salmon", "fish")),
    ("🍤", ("кревет", "shrimp")),
    ("🍗", ("куриц", "курин", "наггетс", "индейк", "тендер", "chicken", "tender")),
    ("🥩", ("говяд", "говяж", "стейк", "свинин", "мяс", "котлет", "beef", "meat")),
    ("🥓", ("бекон", "ветчин", "колбас", "сосиск", "bacon")),
    ("💪", ("протеин", "protein")),
    ("🥜", ("арахис", "орех", "кешью", "миндал", "фисташ", "peanut", "nut")),
    ("🌱", ("тофу", "темпе", "сейтан", "tofu", "tempeh", "seitan")),
    ("🫘", ("нут", "чечевиц", "фасол", "горох", "боб", "фалафел", "хумус", "бобов")),
    # dairy and bread
    ("🧀", ("сыр", "фета", "моцарел", "творог", "cheese", "feta")),
    ("🥛", ("йогурт", "yogurt")),
    ("🥫", ("соус", "майонез", "кетчуп", "sauce", "ketchup")),
    ("🍞", ("хлеб", "батон", "лепёшк", "bread")),
    ("🍚", ("рис", "rice")),
    # fruit
    ("🍌", ("банан", "banana")),
    ("🍎", ("яблок", "яблоч", "apple")),
    ("🍊", ("мандарин", "апельсин", "orange")),
    ("🍍", ("ананас", "pineapple")),
    ("🍓", ("клубник", "strawberr")),
    ("🫐", ("голубик", "черник", "blueberr")),
    ("🥝", ("киви", "kiwi")),
    ("🍇", ("виноград", "grape")),
    ("🍑", ("персик", "peach")),
    ("🍉", ("арбуз", "melon")),
    ("🥑", ("авокад", "avocado")),
    ("🐉", ("питахай", "драконов")),
    ("🍒", ("вишн", "черешн", "рамбутан", "личи", "ягод", "cherr", "berr")),
    # vegetables
    ("🍅", ("помидор", "томат", "tomato")),
    ("🥒", ("огур", "cucumber")),
    ("🥕", ("морков", "carrot")),
    ("🥔", ("картош", "картоф", "батат", "potato")),
    ("🧄", ("чеснок", "чесноч", "garlic")),
    ("🫒", ("олив", "olive")),
    ("🍄", ("гриб", "шампиньон", "mushroom")),
    ("🌽", ("кукуруз", "corn")),
    ("🫛", ("горош", "pea")),
    ("🥬", ("кейл", "капуст", "шпинат", "зелень", "kale", "lettuce")),
    ("🥦", ("брокол", "овощ", "смесь", "vegetable")),
    ("🌿", ("трав", "приправ", "специ", "корица", "herb", "spice")),
)

_WORD = re.compile(r"[a-zа-яё&]+")


def _words(name: str) -> list:
    return _WORD.findall(name.casefold().replace("ё", "е"))


def _norm(stem: str) -> str:
    return stem.casefold().replace("ё", "е")


_STEMS = tuple((emoji, tuple(_norm(stem) for stem in stems)) for emoji, stems in _TABLE)


def lead(name: Optional[str]) -> str:
    """What a button puts before the dish's name: its emoji and ``SEP``."""
    return of_name(name) + SEP


def of_name(name: Optional[str]) -> str:
    """The emoji for a dish called ``name``; ``DEFAULT`` when nothing in it is known."""
    if not name:
        return DEFAULT
    words = _words(name)
    text = " ".join(words)
    for emoji, stems in _STEMS:
        for stem in stems:
            # A stem with a space or a sign is matched in the whole name.
            if " " in stem or "&" in stem:
                if stem in text:
                    return emoji
            elif any(word.startswith(stem) for word in words):
                return emoji
    return DEFAULT
