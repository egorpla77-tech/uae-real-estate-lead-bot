import html
import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class LeadSignal:
    kind: str
    phrase: str
    temperature: str
    score: int
    origin: str
    vehicle: str
    destination: str


DESTINATIONS = (
    (("dubai", "дубай", "дубае", "дубая", "дубаи", "dxb"), "Дубай"),
    (("abu dhabi", "abu-dhabi", "абу даби", "абу-даби"), "Абу-Даби"),
    (("ras al khaimah", "ras-al-khaimah", "рас эль хайм", "рас-эль-хайм"), "Рас-эль-Хайма"),
    (("sharjah", "шардж"), "Шарджа"),
    (("ajman", "аджман"), "Аджман"),
    (("uae", "u.a.e", "оаэ", "эмират"), "ОАЭ"),
)

PROPERTY_ALIASES = (
    (("studio", "студи"), "студия"),
    (("apartment", "flat", "квартир", "апартамент"), "квартира"),
    (("villa", "вилл"), "вилла"),
    (("townhouse", "таунхаус"), "таунхаус"),
    (("penthouse", "пентхаус"), "пентхаус"),
    (("commercial", "коммерческ", "офис", "retail"), "коммерческая недвижимость"),
    (("property", "real estate", "realty", "недвижим"), "недвижимость"),
)

PROPERTY_MARKERS = tuple(alias for aliases, _label in PROPERTY_ALIASES for alias in aliases)
UAE_MARKERS = tuple(alias for aliases, _label in DESTINATIONS for alias in aliases)

PRICE_PHRASES = (
    "какая цена",
    "цена",
    "стоимость",
    "сколько стоит",
    "сколько будет стоить",
    "сколько",
    "почем",
    "по чем",
    "прайс",
    "price",
    "how much",
    "cost",
)
HANDOVER_PHRASES = (
    "срок сдачи",
    "когда сдача",
    "когда сдается",
    "когда сдаётся",
    "когда будет готов",
    "готовность",
    "срок строительства",
    "срок",
    "handover",
    "completion date",
    "ready when",
)
LAYOUT_PHRASES = (
    "планировка",
    "планировки",
    "планировку",
    "план этажа",
    "сколько спален",
    "сколько комнат",
    "какая площадь",
    "метраж",
    "floor plan",
    "layout",
    "bedroom",
    "br",
)
FINANCE_PHRASES = (
    "можно в ипотеку",
    "ипотека",
    "ипотеку",
    "рассрочка",
    "рассрочку",
    "первоначальный взнос",
    "первый взнос",
    "график платеж",
    "условия оплаты",
    "платежный план",
    "payment plan",
    "mortgage",
    "installment",
    "instalment",
    "down payment",
)
BUY_PHRASES = (
    "хочу купить",
    "хотим купить",
    "ищу квартиру",
    "ищу недвижимость",
    "интересует покупка",
    "подберите",
    "нужна квартира",
    "нужна вилла",
    "рассматриваю покупку",
    "готов купить",
    "want to buy",
    "looking to buy",
    "looking for an apartment",
    "interested in buying",
)
CONSULT_PHRASES = (
    "подскажите",
    "расскажите",
    "можно подробнее",
    "подробности",
    "актуально",
    "есть варианты",
    "какие варианты",
    "что входит",
    "как оформить",
    "как купить",
    "можно иностранцу",
    "details",
    "more info",
    "available",
    "is it available",
)

SELF_PROMO_MARKERS = (
    "пишите в личку",
    "пишите в лс",
    "звоните",
    "whatsapp",
    "ватсап",
    "оставьте заявку",
    "наша компания",
    "наше агентство",
    "мы подберем",
    "мы подберём",
    "комиссия агента",
    "подписывайтесь",
    "ссылка в профиле",
    "dm me",
    "contact me",
    "our agency",
    "limited offer",
)
SELLER_MARKERS = (
    "продаю",
    "продам",
    "сдаю",
    "сдам",
    "собственник продает",
    "собственник продаёт",
    "предлагаю купить",
    "выставил на продажу",
    "for sale by owner",
    "i am selling",
    "agent listing",
)
AGENT_LISTING_MARKERS = (
    "updated price",
    "update price",
    "price updated",
    "asking price",
    "we pay",
    "commission",
    "комисси",
    "top-up",
    "top up",
    "distress deal",
    "distress opportunity",
    "current market value",
    "submit a competitive offer",
    "on your behalf",
    "urgent buyer requirement",
    "buyer requirement",
    "seller required",
    "sellers welcome",
    "agents welcome",
    "direct owners",
    "direct owner",
    "rented",
    "tenanted",
    "vacant on transfer",
    "viewing is required",
    "contact:",
)
NON_TARGET_MARKERS = (
    "вакансия",
    "ищем брокера",
    "требуется риелтор",
    "обучение риелторов",
    "курс риелтора",
    "туры в дубай",
    "горящий тур",
    "отель",
    "автомобиль",
    "машина",
    "работа в дубае",
)

PHONE_RE = re.compile(r"(?:\+\d[\d\s()\-]{8,}\d)")
URL_RE = re.compile(r"(?:https?://|t\.me/|vk\.com/)", re.IGNORECASE)
MONEY_RE = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*(?:aed|usd|eur|дирхам|доллар|млн|миллион|тыс|₽|\$|€)",
    re.IGNORECASE,
)
SIZE_RE = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:м2|м²|sq\.?\s*ft|sqft|sqm)\b", re.IGNORECASE)
SHORT_INTENT_RE = re.compile(
    r"^(?:а\s+)?(?:цена|стоимость|сколько|почем|по чем|срок сдачи|срок|планировка|"
    r"ипотека|рассрочка|первый взнос|подробности|актуально|price|how much|handover|"
    r"layout|mortgage|payment plan|details|available)\s*[?!.]*$",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    return " ".join(html.unescape(value or "").replace("\xa0", " ").split())


def lowered(value: str) -> str:
    return normalize_text(value).lower().replace("ё", "е")


def first_marker(text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        if marker.replace("ё", "е") in text:
            return marker
    return ""


def first_phrase(text: str, phrases: tuple[str, ...]) -> str:
    for phrase in phrases:
        normalized = phrase.replace("ё", "е")
        if re.search(rf"(?<!\w){re.escape(normalized)}(?!\w)", text):
            return phrase
    return ""


def has_any(text: str, markers: tuple[str, ...]) -> bool:
    return bool(first_marker(text, markers))


def detect_destination(text: str) -> str:
    compact = lowered(text)
    for aliases, label in DESTINATIONS:
        if any(alias in compact for alias in aliases):
            return label
    return ""


def detect_object(text: str) -> str:
    compact = lowered(text)
    for aliases, label in PROPERTY_ALIASES:
        if any(alias in compact for alias in aliases):
            return label
    return "недвижимость"


def is_self_promo(normalized: str, text: str) -> bool:
    promo_hits = sum(1 for marker in SELF_PROMO_MARKERS if marker.replace("ё", "е") in text)
    contacts = len(URL_RE.findall(normalized)) + len(PHONE_RE.findall(normalized))
    return promo_hits >= 2 or (promo_hits >= 1 and contacts >= 1) or contacts >= 3


def is_agent_listing(normalized: str, text: str) -> bool:
    hits = sum(1 for marker in AGENT_LISTING_MARKERS if marker in text)
    if hits >= 2:
        return True
    if hits and (PHONE_RE.search(normalized) or URL_RE.search(normalized)):
        return True
    return bool(hits and len(normalized) >= 180 and "?" not in normalized)


def classify_lead(value: str, context: str = "", source_title: str = "") -> Tuple[Optional[LeadSignal], str]:
    normalized = normalize_text(value)
    text = lowered(normalized)
    context_text = lowered(f"{context} {source_title}")
    combined = f"{text} {context_text}".strip()

    if not text:
        return None, "empty"
    if len(normalized) > 1400:
        return None, "too_long"
    if has_any(text, NON_TARGET_MARKERS):
        return None, "non_target"
    if is_self_promo(normalized, text):
        return None, "self_promo"
    if is_agent_listing(normalized, text):
        return None, "agent_listing"

    destination = detect_destination(text) or detect_destination(context_text)
    if not destination:
        return None, "uae_not_found"
    if not has_any(combined, PROPERTY_MARKERS):
        return None, "no_real_estate_context"

    short_intent = bool(SHORT_INTENT_RE.match(normalized))
    price_phrase = first_phrase(text, PRICE_PHRASES)
    handover_phrase = first_phrase(text, HANDOVER_PHRASES)
    layout_phrase = first_phrase(text, LAYOUT_PHRASES)
    finance_phrase = first_phrase(text, FINANCE_PHRASES)
    buy_phrase = first_phrase(text, BUY_PHRASES)
    consult_phrase = first_phrase(text, CONSULT_PHRASES)
    question = "?" in normalized or text.startswith(
        ("как ", "какая ", "какие ", "сколько ", "можно ", "есть ", "когда ", "what ", "how ", "is ")
    )
    personal = has_any(
        f" {text} ",
        (
            " я ",
            " мне ",
            " нам ",
            " хочу",
            " хотим",
            " ищу",
            " нужен",
            " нужна",
            " интересует",
            " рассматриваю",
            " i ",
        ),
    )

    if has_any(text, SELLER_MARKERS) and not (question or buy_phrase):
        return None, "seller_or_listing"
    if not short_intent and not question and not personal and not buy_phrase:
        return None, "listing_or_ad"
    if not any(
        (
            short_intent,
            price_phrase,
            handover_phrase,
            layout_phrase,
            finance_phrase,
            buy_phrase,
            consult_phrase,
        )
    ):
        return None, "no_buyer_intent"

    if finance_phrase:
        kind, phrase = "financing", finance_phrase
    elif price_phrase or (short_intent and has_any(text, ("цена", "стоимость", "сколько", "price", "how much"))):
        kind, phrase = "price", price_phrase or "цена"
    elif handover_phrase:
        kind, phrase = "handover", handover_phrase
    elif layout_phrase:
        kind, phrase = "layout", layout_phrase
    elif buy_phrase:
        kind, phrase = "purchase", buy_phrase
    else:
        kind, phrase = "consultation", consult_phrase or text[:60]

    score = 2
    if short_intent or question:
        score += 1
    if personal or buy_phrase:
        score += 1
    if buy_phrase:
        score += 1
    if price_phrase or finance_phrase or MONEY_RE.search(text):
        score += 1
    if handover_phrase or layout_phrase or SIZE_RE.search(text):
        score += 1
    if detect_destination(text):
        score += 1
    if PHONE_RE.search(normalized):
        score += 1

    return LeadSignal(
        kind=kind,
        phrase=phrase,
        temperature="hot" if score >= 5 else "warm",
        score=score,
        origin="Недвижимость ОАЭ",
        vehicle=detect_object(combined),
        destination=destination,
    ), "matched"


def classify_instagram_lead(value: str, context: str = "", source_title: str = "") -> Tuple[Optional[LeadSignal], str]:
    return classify_lead(value, context=context, source_title=source_title)
