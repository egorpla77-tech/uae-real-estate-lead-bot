import argparse
import hashlib
import html
import json
import logging
import os
import signal
import sys
import threading
import time
import warnings
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

warnings.filterwarnings("ignore", message=r"urllib3 .* doesn't match a supported version!")
import requests

from classifier import LeadSignal, classify_instagram_lead, classify_lead, normalize_text
from instagram_collector import (
    DEFAULT_INSTAGRAM_HASHTAGS,
    DEFAULT_INSTAGRAM_SOURCES,
    InstagramCandidate,
    InstagramCollector,
    parse_hashtags,
    parse_instagram_sources,
)
from sheets_sync import SheetsSync
from regional_sources import load_regional_sources, source_city_map, source_names
from source_discovery import DiscoveryCandidate, SourceDiscovery, load_catalog
from storage import AccessStore, JsonStore, LeadStore, lead_dedupe_keys
from telegram_collector import (
    TelegramCandidate,
    TelegramCollector,
    collector_from_env,
    regional_collector_from_env,
)


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ENV_PATH = BASE_DIR / ".env"
ACCESS_PATH = DATA_DIR / "access.json"
LEADS_PATH = DATA_DIR / "leads.json"
CURSOR_PATH = DATA_DIR / "source_cursor.json"
DISCOVERY_STATE_PATH = DATA_DIR / "source_discovery.json"
DISCOVERY_CATALOG_PATH = BASE_DIR / "discovery_candidates.json"
REGIONAL_CATALOG_PATH = BASE_DIR / "regional_sources.json"
LOG_PATH = BASE_DIR / "bot.log"

VK_API_URL = "https://api.vk.com/method"
TG_API_URL = "https://api.telegram.org/bot{token}/{method}"
ACCESS_LIMIT = 2

# VK sources for modular-house construction in Moscow and Saint Petersburg.
# The list can be fully replaced with VK_SOURCES in .env.
DEFAULT_SOURCES = [
    'svod_stroy',
    'lebedevbrus',
    'katalog_domov',
    'woodholz',
    'dom.north.forest',
    'derevodomstroy',
    'stosrubov',
    'olimpre',
    'dom_iz_kleenogo_brusa_cena',
    'rubimsrub59',
    'divo35_ru',
    'baskoestate',
    'domoskovie',
    'mpd_rf',
    'n_ville',
    'prodommsk',
    'drevo_35',
    'sk_brigada',
    'treststroy76ru',
    'stroymdom50',
    'ko_brus',
    'brusframe',
    'smitdom',
    'areaxxii',
    'karkasnye_doma_msk',
    'domproteplo',
    'vvstroy',
    'rubkoff',
    'proektdoma',
    'homespro',
    'vologda_brus',
    'stroybrus',
    'club112768924',
    'proekti_domov_is_brusa_odinetazh',
    'club50948792',
    'dom_750',
    'articomphort',
    'domakarkas_ru',
    'gksstroi',
    'sk_tvoy_dom',
    'domaizbrysa',
    'stroitelstvohramov',
    'dsk_vira',
    'dom_mmay',
    'lesdok',
    'skhoromy',
    'sewzod',
    'starkwood_ru',
    'club186334443',
    'club177857762',
    'kaizerdom',
    'domoryad',
    'woodenhouse_ru',
    '53stroy',
    'leskon',
    'domdliavas.brus',
    'kostromskoyterem',
    'perspectiva_dom',
    'svdom2003',
    'domgrad77',
    'suhoy.brus',
    'woodplace.home',
    'dombrevno_ru',
    'terem_stroitelnaya_kompaniya',
    'doma_iz_brusa_v_bashkirii_ceny',
    'hasko_russia',
    'sk_barin',
    'msrhaus',
    'domizbrusa1',
    'batura_karkasnik',
    'msk.evrodom',
    'club21035897',
    'doma_iz_brusa_beloreck',
    'club_gksstroy_msk',
    'doma_iz_brusa_za_materinskij_kap',
    'vvcdomctoy',
    'doma_iz_brusa_video_strojki',
    'derevosrub',
    'doma_iz_brusa_bashkirija',
    'doma_iz_brusa_velikij_novgorod',
    'domperspektiva',
    'doma_iz_brusa_vnutrennjaja_otdel',
    'teremofi',
    'goldenlog',
    'pbkdoma',
    'podkovahouse',
    'kalitka_sk',
    'karkasprojects',
    'baikaldom38',
    'yukanit',
    'konstantastroy',
    'ddmstroyru',
    'mira.groupp',
    'blago.house',
    'mwdom',
    'karkas_dom_pro',
    'concordlife',
    'architectaru',
    'gera.stroit',
    'schoolofdesigning',
    'proekt_invapolis',
    'salmin_company',
    'stroyadom',
    'doma_bako',
    'arhi.begunova',
    'lake_villas_club',
    'avyar_stroy',
    'enjoy_house',
    'arch_akademik',
    'club238528321',
    'sethauze',
    'club237061806',
    'skdommira',
    'nplotnik',
    'easyfab_ru',
    'f.v.elena_1',
    'sddom',
    'karkasgid',
    'topdomrf',
    'midomaru',
    'domforest43',
    '33metra2',
    'dpostroim',
    'clt_construction',
    'dom_grad',
    'allebro1',
    'mmodusdom',
    'houses.ruwoodhousesmag',
    'veha_dom_ru',
    'house_in_kaif',
    'elgumeni',
    'dom9700',
    'veselayastreet',
    'wflumber',
    'wsmodule',
    'rokios_sk',
    'domastroikacom',
    'stroydomabani_ru',
    'club230508792',
    'well__house',
    'safehousemsk',
    'pc_komfort',
    'energyenegeneer',
    'energyvent',
    'energyotoplenie',
    'energyvodosnabgenie',
    'framehh',
    'energykondey',
    'energykanalizaciya',
    'tvoridoma',
    'karkasnye_doma_pod_klych',
    'tri_stroitelya',
    'molotokdom',
    'finskiedoma',
    'zagorodnoestroitelstvoiproekty',
    'club10762324',
    'fmstroyspb',
    'skstroydom',
    'asutalo',
    'izba47',
    'domov_stroitelstvo',
    'hyttehouseru',
    'sk_venets',
    'doma_pestovo_rf',
    'projects_house',
    'skpel',
    'greensideru',
    'exima_house',
    'tasstroy',
    'domizsippaneley',
    'monolithouse',
    'stroitelstvodomru',
    'arhtect',
    'sckasksd',
    'msdstroy',
    'club59529063',
    'lespromspb',
    'stroikahouse',
    'gpdevelopment',
    'prostor.house',
    'club230715640',
    'stroitelstvodomovspb',
    'karkasniki',
    'artdomaru',
    'sruby_domov_i_ban',
    'club4571777',
    'forest.house_spb',
    'moduldom98',
    'proekt_karkas_1',
    'club19276654',
    'panfilovdom',
    'lenstroydom',
    'zastroykaprojekt',
    'karelestroy',
    'domikvol',
    'takedom',
    'villozihouse_sip',
    'rosdomspb',
    'keilstroy',
    'club107760842',
    'sevplotnik',
    'club_sampo_house',
    'rdsspb',
    'belovtao',
    'profbrusdomspb',
    'russhouse',
    'artbook.house',
    'udom_spb',
    'stroitelstvodomovizbrusa',
    'format_house_spb',
    'rsu21',
    'skgarmoniyadom',
    'club146513199',
    'stroyca78',
    'elkhouse',
    'domaizbrusa78',
    'sk_chastdushi',
    'zagorodnoe_stroitelstvo_domov',
    'liveinwood',
    'club10962619',
    '47dom',
    'zd_spb',
    'sk_snetkov',
    'petrov_stroit',
    'certushaus',
    'elbrus.house',
    'barberrystroy',
    'profdom_spb_msc',
    'chestnoestroy',
    'skzakazdomov',
    'holzhouse_doma',
    'belostrov78',
    'club230693685',
    'club11733356',
    'vashdom_prosto',
    'strois_spb',
    'karkasstroyspb',
    'dom_stroy_gorspb',
    'mirdomovspb',
    'fathers.house812',
    'katsayconstruction',
    'scandiecodom',
    'ilrusstroy',
    'velesadom',
    'skbast',
    'stroylite',
    'sk_dynasty',
    'vertdom',
    'holtsovhouse',
    'club250367',
    'karkasdomskdomostroi',
    'sk_stroyind',
    'club30306060',
    'glavnymetr',
    'woodius',
    'club147063096',
    'goodkarkasspb',
    'ddom47',
    'nordsrub',
    'kedrhousespb',
    'passivehouseprojects',
    'era_house',
    'novator_ltd',
    'club238054238',
    'club382108',
    'pdsgroupspb',
    'rusdom35',
    'katalog035',
    'seahomeresort',
    'stroy_sp',
    'domgazobeton',
    'domdachastroy',
    'zagorodnydom53',
    'shans_spb',
    'vivahaus',
    'eskspbru',
    'club228479325',
    'keystroy_ru',
    '53mastera',
    'zagorodstroika',
    'exposferaspb',
    'ckmk.group',
    'letwoodgroup',
    'timatalo',
    'chalet_modul',
    'karkasniydomspb',
    'proyektdacha',
    'kvartal.center',
    'postroimechtuspb',
    'zagorodnaya_studia',
    'tophouse_official',
    'pslcomp',
    'skevrodom',
    'ckludmila',
    'arbobalt',
    'finskidomik',
    'stroitelstvodomov.izgazobetona',
    'ipmodul',
    'nedvijka_spb',
]
# Active and recently discovered VK sources for UAE real estate. The larger
# discovery catalog is checked separately and can promote additional groups.
DEFAULT_SOURCES = [
    "club166578517",
    "insiderealty_ru",
    "axcapital_cis",
    "dubai_realty_com",
    "yes.realty",
    "nedvizhimost89",
    "dubaipropertyforyou",
    "dubaiarenda",
    "kseniazhikhareva",
    "dubailuxuryhomesanv",
    "novostroykiss",
    "smartinvest_dubai",
    "realivlev",
    "apartment_time",
    "zimadubai",
    "club60822449",
    "etagi_dubai",
    "samoletpluscherepovets35",
    "orlovpro",
    "2drealty",
    "dom_dubai",
    "medeyadxb_ru",
    "firstdubaiproperty",
    "ovcharenkorealestate",
    "billionacedubai",
    "apartmentsdubaiturkey",
    "mira_estate_uae",
    "uae.dubai.invest",
    "dubai_realty_com",
    "dubairealestate_space",
    "realestate99",
]
@dataclass(frozen=True)
class Source:
    screen_name: str
    group_id: int
    title: str
    city: str = ""


@dataclass(frozen=True)
class Hit:
    uid: str
    source_title: str
    source_url: str
    text: str
    author_url: str
    direct_url: str
    created_at: int
    signal: LeadSignal
    segment: str
    source_city: str = ""


class StopSignal:
    def __init__(self) -> None:
        self._event = threading.Event()

    def stop(self, *_args: Any) -> None:
        self._event.set()

    def wait(self, seconds: float) -> bool:
        return self._event.wait(seconds)

    @property
    def is_set(self) -> bool:
        return self._event.is_set()


def setup_logging() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stdout)],
    )


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def read_env_value(path: Path, wanted_key: str) -> str:
    """Читает один секрет из старого .env, не импортируя его настройки проекта."""
    if not path.exists():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == wanted_key:
            return value.strip().strip('"').strip("'")
    return ""


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "да", "on"}


def requests_proxy_from_env() -> Dict[str, str]:
    proxy = (os.getenv("TELEGRAM_BOT_PROXY") or os.getenv("TELEGRAM_PROXY") or "").strip()
    if not proxy.lower().startswith(("http://", "https://")):
        return {}
    return {"http": proxy, "https": proxy}


def parse_csv(raw: str) -> List[str]:
    result: List[str] = []
    seen = set()
    for item in raw.replace("\n", ",").split(","):
        value = item.strip()
        if value and value.lower() not in seen:
            result.append(value)
            seen.add(value.lower())
    return result


def clean_vk_token(token: str) -> str:
    return token.split("&", 1)[0].strip()


def parse_vk_tokens(*raw_values: str) -> List[str]:
    tokens: List[str] = []
    seen = set()
    for raw in raw_values:
        for item in str(raw or "").replace(";", ",").replace("\n", ",").split(","):
            token = clean_vk_token(item)
            if token and token not in seen:
                tokens.append(token)
                seen.add(token)
    return tokens


def lead_id_for_uid(uid: str) -> str:
    return hashlib.sha1(uid.encode("utf-8", errors="ignore")).hexdigest()[:12]


def vk_author_url(author_id: int) -> str:
    return f"https://vk.com/id{author_id}" if author_id > 0 else ""


def source_url(source: Source) -> str:
    return f"https://vk.com/{source.screen_name}"


def wall_post_url(group_id: int, post_id: int) -> str:
    return f"https://vk.com/wall-{group_id}_{post_id}"


def wall_comment_url(group_id: int, post_id: int, comment_id: int) -> str:
    return f"https://vk.com/wall-{group_id}_{post_id}?reply={comment_id}"


def topic_url(group_id: int, topic_id: int, comment_id: int) -> str:
    return f"https://vk.com/topic-{group_id}_{topic_id}?post={comment_id}"


class VkClient:
    def __init__(self, tokens: List[str], api_version: str, request_delay: float) -> None:
        self.tokens = [clean_vk_token(token) for token in tokens if clean_vk_token(token)]
        if not self.tokens:
            raise RuntimeError("Не найден VK_TOKEN/VK_TOKENS")
        self._token_index = 0
        self.api_version = api_version
        self.request_delay = request_delay
        self.session = requests.Session()

    @property
    def token_count(self) -> int:
        return len(self.tokens)

    def _next_token(self) -> str:
        token = self.tokens[self._token_index % len(self.tokens)]
        self._token_index += 1
        return token

    def _redact_tokens(self, value: str) -> str:
        result = value
        for token in self.tokens:
            result = result.replace(token, "[redacted]")
        return result

    def request(self, method: str, params: Dict[str, Any]) -> Optional[Any]:
        for attempt in range(1, 4):
            token = self._next_token()
            payload = {**params, "access_token": token, "v": self.api_version}
            try:
                response = self.session.get(f"{VK_API_URL}/{method}", params=payload, timeout=30)
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                safe_error = self._redact_tokens(str(exc))
                logging.warning("VK %s: попытка %s/3: %s", method, attempt, safe_error)
                time.sleep(min(attempt * 2, 6))
                continue
            finally:
                time.sleep(self.request_delay)

            if "error" not in data:
                return data.get("response")
            error = data["error"]
            code = int(error.get("error_code", 0) or 0)
            if code not in {15, 100}:
                logging.warning("VK %s: ошибка %s — %s", method, code, error.get("error_msg"))
            if code in {6, 9, 10, 29}:
                time.sleep(2 + attempt * 2)
                continue
            return None
        return None

    def resolve_sources(self, names: List[str]) -> List[Source]:
        response = self.request("groups.getById", {"group_ids": ",".join(names[:500]), "fields": "screen_name,city"})
        groups = response.get("groups", []) if isinstance(response, dict) else response if isinstance(response, list) else []
        result: List[Source] = []
        for group in groups:
            group_id = int(group.get("id", 0) or 0)
            if group_id > 0 and not int(group.get("is_closed", 0) or 0):
                result.append(
                    Source(
                        screen_name=str(group.get("screen_name") or f"club{group_id}"),
                        group_id=group_id,
                        title=str(group.get("name") or f"club{group_id}"),
                        city=str((group.get("city") or {}).get("title") or ""),
                    )
                )
        return result

    def wall_posts(self, source: Source, count: int) -> Iterable[Dict[str, Any]]:
        response = self.request("wall.get", {"owner_id": -source.group_id, "count": count, "filter": "all"})
        yield from (response.get("items", []) if isinstance(response, dict) else [])

    def wall_comments(
        self,
        source: Source,
        post_id: int,
        since_ts: int,
        max_items: int = 0,
    ) -> Iterable[Dict[str, Any]]:
        offset = 0
        yielded = 0
        while True:
            response = self.request(
                "wall.getComments",
                {
                    "owner_id": -source.group_id,
                    "post_id": post_id,
                    "count": 100,
                    "offset": offset,
                    "sort": "desc",
                    "thread_items_count": 10,
                },
            )
            items = response.get("items", []) if isinstance(response, dict) else []
            if not items:
                return
            oldest = int(time.time())
            for comment in items:
                created = int(comment.get("date", 0) or 0)
                oldest = min(oldest, created)
                if created >= since_ts:
                    yield comment
                    yielded += 1
                    if max_items and yielded >= max_items:
                        return
                for nested in (comment.get("thread") or {}).get("items", []) or []:
                    nested_created = int(nested.get("date", 0) or 0)
                    oldest = min(oldest, nested_created)
                    if nested_created >= since_ts:
                        yield nested
                        yielded += 1
                        if max_items and yielded >= max_items:
                            return
            if oldest < since_ts or len(items) < 100:
                return
            offset += 100

    def board_topics(self, source: Source, since_ts: int) -> Iterable[Dict[str, Any]]:
        response = self.request(
            "board.getTopics",
            {"group_id": source.group_id, "count": 100, "order": 1, "extended": 1},
        )
        items = response.get("items", []) if isinstance(response, dict) else []
        for topic in items:
            if int(topic.get("updated", 0) or topic.get("created", 0) or 0) >= since_ts:
                yield topic

    def board_comments(self, source: Source, topic_id: int, since_ts: int) -> Iterable[Dict[str, Any]]:
        offset = 0
        while True:
            response = self.request(
                "board.getComments",
                {
                    "group_id": source.group_id,
                    "topic_id": topic_id,
                    "count": 100,
                    "offset": offset,
                    "sort": "desc",
                    "extended": 1,
                },
            )
            items = response.get("items", []) if isinstance(response, dict) else []
            if not items:
                return
            oldest = int(time.time())
            for comment in items:
                created = int(comment.get("date", 0) or 0)
                oldest = min(oldest, created)
                if created >= since_ts:
                    yield comment
            if oldest < since_ts or len(items) < 100:
                return
            offset += 100


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.token = token.strip()
        self.session = requests.Session()
        self.proxies = requests_proxy_from_env()
        self.offset = 0

    def request(self, method: str, payload: Dict[str, Any], timeout: int = 30) -> Optional[Any]:
        for attempt in range(1, 4):
            try:
                response = self.session.post(
                    TG_API_URL.format(token=self.token, method=method),
                    json=payload,
                    timeout=timeout,
                    proxies=self.proxies or None,
                )
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                safe_error = str(exc).replace(self.token, "[redacted]")
                logging.warning("Telegram %s: попытка %s/3: %s", method, attempt, safe_error)
                time.sleep(min(attempt * 2, 6))
                continue
            if data.get("ok"):
                return data.get("result")
            description = str(data.get("description", data)).replace(self.token, "[redacted]")
            logging.warning("Telegram %s: %s", method, description)
            return None
        return None

    def send_message(
        self,
        target: str,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
    ) -> Optional[Any]:
        if not target:
            return None
        payload: Dict[str, Any] = {
            "chat_id": target,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self.request("sendMessage", payload)

    def get_updates(self, timeout: int = 25) -> List[Dict[str, Any]]:
        result = self.request(
            "getUpdates",
            {
                "offset": self.offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=timeout + 10,
        )
        if not isinstance(result, list):
            return []
        for update in result:
            self.offset = max(self.offset, int(update.get("update_id", 0)) + 1)
        return result

    def answer_callback(self, callback_id: str, text: str) -> None:
        self.request("answerCallbackQuery", {"callback_query_id": callback_id, "text": text}, timeout=15)

    def edit_reply_markup(self, chat_id: str, message_id: int, reply_markup: Dict[str, Any]) -> None:
        self.request(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup},
            timeout=15,
        )


class Monitor:
    def __init__(self) -> None:
        load_env(ENV_PATH)
        legacy_path = os.getenv("LEGACY_ENV_PATH", "").strip()
        if legacy_path:
            if not os.getenv("VK_TOKEN"):
                legacy_token = read_env_value(Path(legacy_path), "VK_TOKEN")
                if legacy_token:
                    os.environ["VK_TOKEN"] = legacy_token
            if not os.getenv("VK_TOKENS"):
                legacy_tokens = read_env_value(Path(legacy_path), "VK_TOKENS")
                if legacy_tokens:
                    os.environ["VK_TOKENS"] = legacy_tokens

        vk_tokens = parse_vk_tokens(os.getenv("VK_TOKEN", ""), os.getenv("VK_TOKENS", ""))
        tg_token = os.getenv("TG_BOT_TOKEN", "")
        if not vk_tokens:
            raise RuntimeError("Не найден VK_TOKEN/VK_TOKENS: задайте его в .env или LEGACY_ENV_PATH")
        if not tg_token:
            raise RuntimeError("Не задан TG_BOT_TOKEN в .env")

        self.lookback_hours = max(1, env_int("LOOKBACK_HOURS", 48))
        self.scan_interval = max(60, env_int("SCAN_INTERVAL_SECONDS", 300))
        self.wall_posts_limit = max(1, min(100, env_int("WALL_POSTS_LIMIT", 25)))
        self.priority_batch_size = max(1, env_int("PRIORITY_BATCH_SIZE", 20))
        self.market_batch_size = max(1, env_int("MARKET_BATCH_SIZE", 4))
        self.regional_batch_size = max(1, env_int("REGIONAL_BATCH_SIZE", 20))
        self.request_delay = max(0.2, env_float("VK_REQUEST_DELAY_SECONDS", 0.55))
        self.allow_group_posts = env_bool("ALLOW_GROUP_POSTS", False)
        self.scan_board_topics = env_bool("SCAN_BOARD_TOPICS", True)
        self.discovery_enabled = env_bool("SOURCE_DISCOVERY_ENABLED", True)
        self.discovery_batch_size = max(0, env_int("SOURCE_DISCOVERY_BATCH_SIZE", 10))
        self.discovery_post_limit = max(1, min(12, env_int("SOURCE_DISCOVERY_POST_LIMIT", 4)))
        self.discovery_comments_limit = max(1, min(100, env_int("SOURCE_DISCOVERY_COMMENTS_LIMIT", 50)))
        self.discovery_lookback_hours = max(self.lookback_hours, env_int("SOURCE_DISCOVERY_LOOKBACK_HOURS", 336))
        self.discovery_min_matches = max(1, env_int("SOURCE_DISCOVERY_MIN_MATCHED_LEADS", 1))
        self.discovery_retry_seconds = max(3600, env_int("SOURCE_DISCOVERY_RETRY_SECONDS", 604800))
        self.discovery_reject_seconds = max(86400, env_int("SOURCE_DISCOVERY_REJECT_SECONDS", 2592000))
        self.instagram_enabled = False
        self.telegram_user_enabled = env_bool("TELEGRAM_ENABLED", False)
        self.regional_enabled = env_bool("REGIONAL_ENABLED", True)

        self.source_names = parse_csv(os.getenv("VK_SOURCES", ",".join(DEFAULT_SOURCES)))
        extras = parse_csv(os.getenv("VK_SOURCES_EXTRA", ""))
        self.source_names += [item for item in extras if item not in self.source_names]
        regional_vk = load_regional_sources(REGIONAL_CATALOG_PATH, "vk") if self.regional_enabled else []
        regional_telegram = load_regional_sources(REGIONAL_CATALOG_PATH, "telegram") if self.regional_enabled else []
        self.regional_vk_city_map = source_city_map(regional_vk)
        self.regional_telegram_city_map = source_city_map(regional_telegram)
        self.regional_vk_names = {
            item.name.lower()
            for item in regional_vk
        }
        source_keys = {name.lower() for name in self.source_names}
        self.source_names += [
            name for name in source_names(regional_vk) if name.lower() not in source_keys
        ]
        full_catalog = load_catalog(DISCOVERY_CATALOG_PATH, require_target_match=False)
        if env_bool("VK_INCLUDE_ALL_CANDIDATES", True):
            source_keys = {name.lower() for name in self.source_names}
            for candidate in full_catalog:
                if candidate.screen_name.lower() not in source_keys:
                    self.source_names.append(candidate.screen_name)
                    source_keys.add(candidate.screen_name.lower())
        active_keys = {name.lower() for name in self.source_names}
        self.discovery_catalog = [
            candidate for candidate in full_catalog if candidate.screen_name.lower() not in active_keys
        ]
        self.discovery = SourceDiscovery(
            self.discovery_catalog,
            JsonStore(DISCOVERY_STATE_PATH, {"cursor": 0, "candidates": {}}),
            self.discovery_retry_seconds,
            self.discovery_reject_seconds,
        )
        source_keys = {name.lower() for name in self.source_names}
        self.source_names += [item for item in self.discovery.accepted_names() if item.lower() not in source_keys]
        default_competitors = ""
        self.competitor_names = set(parse_csv(os.getenv("VK_COMPETITOR_SOURCES", default_competitors)))
        self.instagram_sources = parse_instagram_sources(
            os.getenv("INSTAGRAM_SOURCES", ""),
            DEFAULT_INSTAGRAM_SOURCES,
        )
        self.instagram_hashtags = parse_hashtags(
            os.getenv("INSTAGRAM_HASHTAGS", ""),
            DEFAULT_INSTAGRAM_HASHTAGS,
        )

        self.access = AccessStore(ACCESS_PATH, limit=ACCESS_LIMIT)
        self.leads = LeadStore(LEADS_PATH)
        self.cursor = JsonStore(CURSOR_PATH, {"priority_cursor": 0, "market_cursor": 0})
        self.vk = VkClient(vk_tokens, os.getenv("VK_API_VERSION", "5.199"), self.request_delay)
        self.sheets = SheetsSync.from_env(BASE_DIR, project="Недвижимость ОАЭ")
        self.instagram: Optional[InstagramCollector] = None
        if self.instagram_enabled:
            self.instagram = InstagramCollector(
                username=os.getenv("INSTAGRAM_USERNAME", ""),
                password=os.getenv("INSTAGRAM_PASSWORD", ""),
                data_dir=DATA_DIR,
                source_names=self.instagram_sources,
                hashtags=self.instagram_hashtags,
                lookback_hours=max(1, env_int("INSTAGRAM_LOOKBACK_HOURS", self.lookback_hours)),
                source_batch_size=max(0, env_int("INSTAGRAM_SOURCE_BATCH_SIZE", 2)),
                hashtag_batch_size=max(0, env_int("INSTAGRAM_HASHTAG_BATCH_SIZE", 1)),
                media_limit=max(1, env_int("INSTAGRAM_MEDIA_LIMIT", 4)),
                comments_limit=max(1, env_int("INSTAGRAM_COMMENTS_LIMIT", 80)),
                media_lookback_days=max(1, env_int("INSTAGRAM_MEDIA_LOOKBACK_DAYS", 21)),
                only_video=env_bool("INSTAGRAM_ONLY_VIDEO", True),
                request_delay=max(0.0, env_float("INSTAGRAM_REQUEST_DELAY_SECONDS", 2.5)),
                request_timeout=max(5.0, env_float("INSTAGRAM_TIMEOUT_SECONDS", 35.0)),
                comment_max_age_days=max(0, env_int("INSTAGRAM_COMMENT_MAX_AGE_DAYS", 5)),
                proxy_url=os.getenv("INSTAGRAM_PROXY", ""),
                allow_graphql_fallback=env_bool("INSTAGRAM_ALLOW_GRAPHQL_FALLBACK", False),
            )
        self.telegram_user: Optional[TelegramCollector] = None
        self.telegram_regional: Optional[TelegramCollector] = None
        if self.telegram_user_enabled:
            self.telegram_user = collector_from_env(BASE_DIR, self.lookback_hours)
            if regional_telegram:
                self.telegram_regional = regional_collector_from_env(
                    BASE_DIR,
                    self.lookback_hours,
                    source_names(regional_telegram),
                    self.regional_telegram_city_map,
                )
        self.telegram = TelegramClient(tg_token)
        self.stop_signal = StopSignal()
        self.scan_lock = threading.Lock()
        self.sources: List[Source] = []

    def prepare(self, validate_telegram: bool = True) -> None:
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self.stop_signal.stop)
            signal.signal(signal.SIGTERM, self.stop_signal.stop)
        if validate_telegram:
            me = self.telegram.request("getMe", {})
            if not me:
                raise RuntimeError("Telegram не принял токен бота")
            logging.info("Telegram-бот подключен: @%s", me.get("username", "unknown"))

        logging.info("Подключение %s источников VK, VK API токенов: %s", len(self.source_names), self.vk.token_count)
        resolved_sources = self.vk.resolve_sources(self.source_names)
        self.sources = [
            Source(
                screen_name=source.screen_name,
                group_id=source.group_id,
                title=source.title,
                city=self.regional_vk_city_map.get(source.screen_name.lower(), source.city),
            )
            for source in resolved_sources
        ]
        if not self.sources:
            raise RuntimeError("Не удалось подключить ни одного сообщества VK")
        logging.info("Подключено источников VK: %s", len(self.sources))
        if self.instagram_enabled and self.instagram:
            logging.info(
                "Instagram настроен: аккаунтов %s, хэштегов %s",
                len(self.instagram.source_names),
                len(self.instagram.hashtags),
            )
        if self.telegram_user_enabled and self.telegram_user:
            logging.info("Telegram user parser настроен: источников %s", len(self.telegram_user.source_names))
        if self.regional_enabled:
            logging.info(
                "Региональный радар: VK=%s, Telegram=%s, городов=%s",
                len(self.regional_vk_names),
                len(self.regional_telegram_city_map),
                len(set(self.regional_vk_city_map.values()) | set(self.regional_telegram_city_map.values())),
            )

    def recipients(self) -> List[str]:
        return self.access.recipients()

    def instagram_source_count(self) -> int:
        if not self.instagram_enabled or not self.instagram:
            return 0
        return len(self.instagram.source_names) + len(self.instagram.hashtags)

    def telegram_source_count(self) -> int:
        if not self.telegram_user_enabled or not self.telegram_user:
            return 0
        regional_count = len(self.telegram_regional.source_names) if self.telegram_regional else 0
        return len(self.telegram_user.source_names) + regional_count

    def broadcast(self, text: str, reply_markup: Optional[Dict[str, Any]] = None) -> int:
        sent = 0
        for chat_id in self.recipients():
            if self.telegram.send_message(chat_id, text, reply_markup=reply_markup):
                sent += 1
            time.sleep(0.15)
        return sent

    def sources_for_scan(self) -> List[Source]:
        regional = [source for source in self.sources if source.screen_name.lower() in self.regional_vk_names]
        priority = [
            source
            for source in self.sources
            if source.screen_name in self.competitor_names and source.screen_name.lower() not in self.regional_vk_names
        ]
        market = [
            source
            for source in self.sources
            if source.screen_name not in self.competitor_names and source.screen_name.lower() not in self.regional_vk_names
        ]
        payload = self.cursor.read()
        payload = payload if isinstance(payload, dict) else {}
        priority_cursor = int(payload.get("priority_cursor", 0) or 0)
        market_cursor = int(payload.get("market_cursor", 0) or 0)
        regional_cursor = int(payload.get("regional_cursor", 0) or 0)
        selected: List[Source] = []

        if priority:
            priority_cursor %= len(priority)
            count = min(self.priority_batch_size, len(priority))
            selected.extend(priority[(priority_cursor + index) % len(priority)] for index in range(count))
            priority_cursor = (priority_cursor + count) % len(priority)
        if market:
            market_cursor %= len(market)
            count = min(self.market_batch_size, len(market))
            selected.extend(market[(market_cursor + index) % len(market)] for index in range(count))
            market_cursor = (market_cursor + count) % len(market)
        if regional:
            regional_cursor %= len(regional)
            count = min(self.regional_batch_size, len(regional))
            selected.extend(regional[(regional_cursor + index) % len(regional)] for index in range(count))
            regional_cursor = (regional_cursor + count) % len(regional)

        self.cursor.write(
            {
                "priority_cursor": priority_cursor,
                "market_cursor": market_cursor,
                "regional_cursor": regional_cursor,
            }
        )
        return selected

    def make_hit(
        self,
        uid: str,
        source: Source,
        text: str,
        context_text: str,
        author_id: int,
        direct_url: str,
        created_at: int,
        stats: Dict[str, int],
    ) -> Optional[Hit]:
        dedupe_keys = lead_dedupe_keys(uid, text=text, lead_url=direct_url, client_url=vk_author_url(author_id))
        if self.leads.is_seen_any(dedupe_keys):
            stats["already_seen"] = stats.get("already_seen", 0) + 1
            return None
        source_context = " ".join(part for part in (source.title, source.city) if part)
        signal_data, reason = classify_lead(text, context=context_text, source_title=source_context)
        stats[reason] = stats.get(reason, 0) + 1
        if not signal_data:
            return None
        is_regional = source.screen_name.lower() in self.regional_vk_names
        if is_regional and source.city:
            signal_data = replace(signal_data, origin=f"{source.city} → ОАЭ")
        if author_id <= 0 and not self.allow_group_posts:
            stats["group_post"] = stats.get("group_post", 0) + 1
            return None
        return Hit(
            uid=uid,
            source_title=source.title,
            source_url=source_url(source),
            text=normalize_text(text),
            author_url=vk_author_url(author_id),
            direct_url=direct_url,
            created_at=created_at,
            signal=signal_data,
            segment=(
                "regional"
                if is_regional
                else "competitor"
                if source.screen_name in self.competitor_names
                else "market"
            ),
            source_city=source.city if is_regional else "",
        )

    def make_instagram_hit(self, candidate: InstagramCandidate, stats: Dict[str, int]) -> Optional[Hit]:
        dedupe_keys = lead_dedupe_keys(
            candidate.uid,
            text=candidate.text,
            lead_url=candidate.direct_url,
            client_url=candidate.author_url,
        )
        if self.leads.is_seen_any(dedupe_keys):
            stats["instagram_already_seen"] = stats.get("instagram_already_seen", 0) + 1
            return None
        signal_data, reason = classify_instagram_lead(
            candidate.text,
            context=candidate.context_text,
            source_title=candidate.source_title,
        )
        stats[f"instagram_{reason}"] = stats.get(f"instagram_{reason}", 0) + 1
        if not signal_data:
            return None
        return Hit(
            uid=candidate.uid,
            source_title=candidate.source_title,
            source_url=candidate.source_url,
            text=normalize_text(candidate.text),
            author_url=candidate.author_url,
            direct_url=candidate.direct_url,
            created_at=candidate.created_at,
            signal=signal_data,
            segment="instagram",
            source_city="",
        )

    def make_telegram_hit(self, candidate: TelegramCandidate, stats: Dict[str, int]) -> Optional[Hit]:
        dedupe_keys = lead_dedupe_keys(
            candidate.uid,
            text=candidate.text,
            lead_url=candidate.direct_url,
            client_url=candidate.author_url,
        )
        if self.leads.is_seen_any(dedupe_keys):
            stats["telegram_already_seen"] = stats.get("telegram_already_seen", 0) + 1
            return None
        signal_data, reason = classify_instagram_lead(
            candidate.text,
            context=candidate.context_text,
            source_title=candidate.source_title,
        )
        stats[f"telegram_{reason}"] = stats.get(f"telegram_{reason}", 0) + 1
        if not signal_data:
            return None
        if candidate.segment == "regional" and candidate.source_city:
            signal_data = replace(signal_data, origin=f"{candidate.source_city} → ОАЭ")
        return Hit(
            uid=candidate.uid,
            source_title=candidate.source_title,
            source_url=candidate.source_url,
            text=normalize_text(candidate.text),
            author_url=candidate.author_url,
            direct_url=candidate.direct_url,
            created_at=candidate.created_at,
            signal=signal_data,
            segment=candidate.segment,
            source_city=candidate.source_city,
        )

    def scan_source(self, source: Source, since_ts: int, stats: Dict[str, int]) -> List[Hit]:
        hits: List[Hit] = []
        posts = list(self.vk.wall_posts(source, self.wall_posts_limit))
        stats["posts_seen"] = stats.get("posts_seen", 0) + len(posts)
        for post in posts:
            post_id = int(post.get("id", 0) or 0)
            created_at = int(post.get("date", 0) or 0)
            author_id = int(post.get("signer_id", 0) or post.get("from_id", 0) or 0)
            post_text = str(post.get("text", ""))
            if created_at >= since_ts:
                hit = self.make_hit(
                    f"wall_post:{source.group_id}:{post_id}",
                    source,
                    post_text,
                    "",
                    author_id,
                    wall_post_url(source.group_id, post_id),
                    created_at,
                    stats,
                )
                if hit:
                    hits.append(hit)

            comments_count = int((post.get("comments") or {}).get("count", 0) or 0)
            if comments_count <= 0:
                continue
            for comment in self.vk.wall_comments(source, post_id, since_ts):
                comment_id = int(comment.get("id", 0) or 0)
                comment_created = int(comment.get("date", 0) or 0)
                comment_author = int(comment.get("from_id", 0) or 0)
                hit = self.make_hit(
                    f"wall_comment:{source.group_id}:{post_id}:{comment_id}",
                    source,
                    str(comment.get("text", "")),
                    post_text,
                    comment_author,
                    wall_comment_url(source.group_id, post_id, comment_id),
                    comment_created,
                    stats,
                )
                if hit:
                    hits.append(hit)

        if self.scan_board_topics:
            for topic in self.vk.board_topics(source, since_ts):
                topic_id = int(topic.get("id", 0) or 0)
                topic_title = str(topic.get("title", ""))
                for comment in self.vk.board_comments(source, topic_id, since_ts):
                    comment_id = int(comment.get("id", 0) or 0)
                    created_at = int(comment.get("date", 0) or 0)
                    author_id = int(comment.get("from_id", 0) or 0)
                    hit = self.make_hit(
                        f"board_comment:{source.group_id}:{topic_id}:{comment_id}",
                        source,
                        str(comment.get("text", "")),
                        topic_title,
                        author_id,
                        topic_url(source.group_id, topic_id, comment_id),
                        created_at,
                        stats,
                    )
                    if hit:
                        hits.append(hit)
        return hits

    def assess_discovery_source(
        self,
        source: Source,
        candidate: DiscoveryCandidate,
        since_ts: int,
        stats: Dict[str, int],
    ) -> Tuple[int, int, int]:
        """Measure demand in a candidate group without emitting its leads before promotion."""
        matched = 0
        sampled_posts = 0
        sampled_comments = 0
        profile_context = " ".join(
            part
            for part in (
                source.title,
                source.city,
                candidate.title,
                candidate.description,
                "ОАЭ Дубай",
            )
            if part
        )
        for post in self.vk.wall_posts(source, self.discovery_post_limit):
            post_id = int(post.get("id", 0) or 0)
            if not post_id:
                continue
            sampled_posts += 1
            post_text = str(post.get("text", ""))
            for comment in self.vk.wall_comments(
                source,
                post_id,
                since_ts,
                max_items=self.discovery_comments_limit,
            ):
                sampled_comments += 1
                signal_data, reason = classify_lead(
                    str(comment.get("text", "")),
                    context=post_text,
                    source_title=profile_context,
                )
                stats[f"discovery_{reason}"] = stats.get(f"discovery_{reason}", 0) + 1
                if signal_data:
                    matched += 1
                    kind_key = f"discovery_signal_{signal_data.kind}"
                    stats[kind_key] = stats.get(kind_key, 0) + 1
        return matched, sampled_posts, sampled_comments

    def run_source_discovery(self, stats: Dict[str, int]) -> None:
        if not self.discovery_enabled or not self.discovery_batch_size or not self.discovery_catalog:
            return
        candidates = self.discovery.select(self.discovery_batch_size, self.source_names)
        if not candidates:
            return
        stats["discovery_candidates"] = len(candidates)
        resolved = {
            source.screen_name.lower(): source
            for source in self.vk.resolve_sources([candidate.screen_name for candidate in candidates])
        }
        since_ts = int((datetime.now(timezone.utc) - timedelta(hours=self.discovery_lookback_hours)).timestamp())
        for candidate in candidates:
            source = resolved.get(candidate.screen_name.lower())
            if not source or not self.source_matches_discovery_region(source, candidate):
                self.discovery.record(candidate, 0, 0, 0, self.discovery_min_matches, unavailable=True)
                stats["discovery_unavailable"] = stats.get("discovery_unavailable", 0) + 1
                continue
            try:
                matched, posts, comments = self.assess_discovery_source(source, candidate, since_ts, stats)
                status = self.discovery.record(
                    candidate,
                    matched,
                    posts,
                    comments,
                    self.discovery_min_matches,
                )
            except Exception:
                logging.exception("Source discovery error for %s", candidate.screen_name)
                self.discovery.record(candidate, 0, 0, 0, self.discovery_min_matches, unavailable=True)
                stats["discovery_error"] = stats.get("discovery_error", 0) + 1
                continue
            stats[f"discovery_{status}"] = stats.get(f"discovery_{status}", 0) + 1
            if status != "accepted":
                continue
            if all(existing.group_id != source.group_id for existing in self.sources):
                self.sources.append(source)
                self.source_names.append(source.screen_name)
                logging.info(
                    "Source discovery added %s: matches=%s posts=%s comments=%s",
                    source.screen_name,
                    matched,
                    posts,
                    comments,
                )

    @staticmethod
    def source_matches_discovery_region(source: Source, candidate: DiscoveryCandidate) -> bool:
        profile = f"{source.screen_name} {source.title} {candidate.title} {candidate.description}".lower()
        has_uae = any(marker in profile for marker in ("дуба", "оаэ", "эмират", "dubai", "uae", "abu dhabi"))
        has_realty = any(
            marker in profile
            for marker in ("недвиж", "квартир", "апартамент", "вилл", "real estate", "realty", "property")
        )
        return has_uae and has_realty

    def collect_hits(self) -> Tuple[List[Hit], Dict[str, int]]:
        since_ts = int((datetime.now(timezone.utc) - timedelta(hours=self.lookback_hours)).timestamp())
        stats: Dict[str, int] = {}
        hits: List[Hit] = []
        selected = self.sources_for_scan()
        logging.info("Проверяется источников: %s", len(selected))
        for source in selected:
            try:
                hits.extend(self.scan_source(source, since_ts, stats))
            except Exception:
                logging.exception("Ошибка при проверке %s", source.screen_name)
        if self.instagram_enabled and self.instagram:
            try:
                instagram_candidates, instagram_stats = self.instagram.collect()
                stats.update({key: stats.get(key, 0) + value for key, value in instagram_stats.items()})
                for candidate in instagram_candidates:
                    hit = self.make_instagram_hit(candidate, stats)
                    if hit:
                        hits.append(hit)
            except Exception:
                stats["instagram_scan_error"] = stats.get("instagram_scan_error", 0) + 1
                logging.exception("Ошибка при проверке Instagram")
        if self.telegram_user_enabled and self.telegram_user:
            try:
                telegram_candidates, telegram_stats = self.telegram_user.collect()
                stats.update({key: stats.get(key, 0) + value for key, value in telegram_stats.items()})
                for candidate in telegram_candidates:
                    hit = self.make_telegram_hit(candidate, stats)
                    if hit:
                        hits.append(hit)
            except Exception:
                stats["telegram_scan_error"] = stats.get("telegram_scan_error", 0) + 1
                logging.exception("Ошибка при проверке Telegram user parser")
        if self.telegram_user_enabled and self.telegram_regional:
            try:
                regional_candidates, regional_stats = self.telegram_regional.collect()
                stats.update(
                    {
                        f"regional_{key}": stats.get(f"regional_{key}", 0) + value
                        for key, value in regional_stats.items()
                    }
                )
                for candidate in regional_candidates:
                    hit = self.make_telegram_hit(candidate, stats)
                    if hit:
                        hits.append(hit)
            except Exception:
                stats["regional_telegram_scan_error"] = stats.get("regional_telegram_scan_error", 0) + 1
                logging.exception("Ошибка при проверке регионального Telegram-радара")
        try:
            self.run_source_discovery(stats)
        except Exception:
            stats["discovery_scan_error"] = stats.get("discovery_scan_error", 0) + 1
            logging.exception("Source discovery cycle failed")
        unique = {hit.uid: hit for hit in hits}
        result = sorted(unique.values(), key=lambda item: item.created_at)
        stats["matched_unique"] = len(result)
        logging.info("Статистика фильтра: %s", json.dumps(stats, ensure_ascii=False, sort_keys=True))
        return result, stats

    def build_hit_message(self, hit: Hit) -> str:
        kind_labels = {
            "price": "Узнаёт цену",
            "handover": "Спрашивает срок сдачи",
            "layout": "Запрашивает планировку",
            "financing": "Интересуется ипотекой/рассрочкой",
            "purchase": "Хочет купить",
            "consultation": "Нужна консультация",
        }
        if hit.segment == "regional":
            header = f"🔥 РЕГИОНАЛЬНЫЙ ЛИД: {hit.source_city or 'РОССИЯ'} → ДУБАЙ"
        elif hit.segment == "competitor":
            header = "⚡ ЛИД У КОНКУРЕНТА"
        elif hit.segment == "instagram":
            header = "📸 ЛИД ИЗ INSTAGRAM"
        elif hit.segment == "telegram":
            header = "💬 ЛИД ИЗ TELEGRAM"
        else:
            header = "🔥 НОВЫЙ ЛИД НА НЕДВИЖИМОСТЬ ОАЭ"
        priority = "🔥 ГОРЯЧИЙ" if hit.signal.temperature == "hot" else "🟡 ТЁПЛЫЙ"
        created = datetime.fromtimestamp(hit.created_at, tz=timezone.utc).astimezone().strftime("%d.%m.%Y %H:%M")
        text = hit.text if len(hit.text) <= 2800 else hit.text[:2799].rstrip() + "…"
        lines = [
            f"<b>{header}</b>",
            "",
            f"<b>Намерение:</b> {html.escape(kind_labels.get(hit.signal.kind, hit.signal.kind))}",
            f"<b>Приоритет:</b> {priority}",
            f"<b>Направление:</b> {html.escape(hit.signal.origin)}",
            f"<b>Объект:</b> {html.escape(hit.signal.vehicle)}",
        ]
        if hit.signal.destination:
            lines.append(f"<b>Город:</b> {html.escape(hit.signal.destination)}")
        if hit.source_city:
            lines.append(f"<b>Регион РФ:</b> {html.escape(hit.source_city)}")
        lines.extend(
            [
                f"<b>Маркер:</b> <code>{html.escape(hit.signal.phrase)}</code>",
                f"<b>Источник:</b> <a href=\"{html.escape(hit.source_url)}\">{html.escape(hit.source_title)}</a>",
                f"<b>Опубликовано:</b> {html.escape(created)}",
                "",
                f"<blockquote>{html.escape(text)}</blockquote>",
                "",
            ]
        )
        if hit.author_url:
            if "instagram.com" in hit.author_url.lower():
                profile_label = "Instagram"
            elif "t.me" in hit.author_url.lower():
                profile_label = "Telegram"
            else:
                profile_label = "VK"
            lines.append(f"<b>Клиент:</b> <a href=\"{html.escape(hit.author_url)}\">открыть профиль {profile_label}</a>")
        lines.append(f"<b>Обращение:</b> <a href=\"{html.escape(hit.direct_url)}\">открыть публикацию</a>")
        return "\n".join(lines)

    def lead_keyboard(self, direct_url: str, lead_id: str, current_status: str = "new") -> Dict[str, Any]:
        labels = {"work": "✅ В работу", "contact": "🤝 Связались", "reject": "🚫 Не лид"}
        status_row = []
        for status in ("work", "contact", "reject"):
            label = labels[status]
            if current_status == status:
                label = "• " + label
            status_row.append({"text": label, "callback_data": f"lead:{status}:{lead_id}"})
        return {"inline_keyboard": [[{"text": "Открыть лид", "url": direct_url}], status_row]}

    def send_hit(self, hit: Hit) -> bool:
        lead_id = lead_id_for_uid(hit.uid)
        sent = self.broadcast(
            self.build_hit_message(hit),
            reply_markup=self.lead_keyboard(hit.direct_url, lead_id),
        )
        if not sent:
            return False
        self.leads.mark_seen(
            hit.uid,
            {
                "lead_id": lead_id,
                "notified_at": int(time.time()),
                "created_at": hit.created_at,
                "kind": hit.signal.kind,
                "status": "new",
                "source": hit.source_title,
                "phrase": hit.signal.phrase,
                "text": hit.text[:1400],
                "url": hit.direct_url,
                "client_url": hit.author_url,
            },
            aliases=lead_dedupe_keys(hit.uid, text=hit.text, lead_url=hit.direct_url, client_url=hit.author_url),
        )
        self.sheets.append_lead(
            lead_id=lead_id,
            created_at=hit.created_at,
            source=hit.source_title,
            source_url=hit.source_url,
            category=hit.signal.kind,
            marker=hit.signal.phrase,
            direction=hit.signal.destination or hit.signal.origin,
            subject=hit.signal.vehicle,
            text=hit.text,
            lead_url=hit.direct_url,
            client_url=hit.author_url,
            status="Новый",
        )
        return True

    def sync_saved_leads_to_sheets(self) -> Tuple[int, int]:
        """Uploads the retained lead history without notifying Telegram recipients again."""
        with self.leads.lock:
            records = list(self.leads.items.items())
        synced = 0
        for uid, item in records:
            lead_id = str(item.get("lead_id") or lead_id_for_uid(uid))
            if self.sheets.append_lead(
                lead_id=lead_id,
                created_at=int(item.get("created_at", item.get("notified_at", 0)) or 0),
                source=str(item.get("source", "")),
                category=str(item.get("kind", "")),
                marker=str(item.get("phrase", "")),
                text=str(item.get("text", "")),
                lead_url=str(item.get("url", "")),
                client_url=str(item.get("client_url", "")),
                status=str(item.get("status", "new")),
            ):
                synced += 1
        return len(records), synced

    def run_scan(self, reason: str = "scheduled", dry_run: bool = False) -> int:
        if not self.scan_lock.acquire(blocking=False):
            return -1
        try:
            logging.info("Запуск проверки: %s", reason)
            hits, _stats = self.collect_hits()
            if dry_run:
                logging.info("Тестовая проверка нашла лидов: %s", len(hits))
                return len(hits)
            if not self.recipients():
                logging.info("Лиды не отправлены: ожидается /start от первого пользователя")
                return 0
            sent = 0
            for hit in hits:
                if self.send_hit(hit):
                    sent += 1
                time.sleep(0.35)
            logging.info("Проверка завершена, отправлено: %s", sent)
            return sent
        except Exception:
            logging.exception("Проверка завершилась с ошибкой")
            return -2
        finally:
            self.scan_lock.release()

    def scan_result_text(self, result: int) -> str:
        if result == -1:
            return "Проверка уже идёт. Дождитесь её завершения."
        if result == -2:
            return "Во время проверки была сетевая ошибка. Бот продолжит работу и попробует снова."
        return f"Проверка завершена. Новых лидов: <b>{result}</b>."

    def pipeline_text(self) -> str:
        counts = self.leads.pipeline_counts(days=30)
        return "\n".join(
            [
                "<b>📊 Воронка лидов на недвижимость ОАЭ за 30 дней</b>",
                "",
                f"🆕 Новые: <b>{counts.get('new', 0)}</b>",
                f"✅ В работе: <b>{counts.get('work', 0)}</b>",
                f"🤝 Связались: <b>{counts.get('contact', 0)}</b>",
                f"🚫 Не лид: <b>{counts.get('reject', 0)}</b>",
                "",
                f"Всего: <b>{counts.get('total', 0)}</b>",
            ]
        )

    def authorized(self, message: Dict[str, Any]) -> bool:
        chat_id = str((message.get("chat") or {}).get("id", ""))
        user_id = int((message.get("from") or {}).get("id", 0) or 0)
        return self.access.is_authorized(user_id, chat_id)

    def welcome(self, chat_id: str, slot: int) -> None:
        self.telegram.send_message(
            chat_id,
            "\n".join(
                [
                    "<b>🔥 Мониторинг лидов на недвижимость ОАЭ активен</b>",
                    "",
                    "Ищу в VK и Telegram людей, которые интересуются недвижимостью Дубая и ОАЭ: ценой, сроком сдачи, планировкой, ипотекой или рассрочкой.",
                    "",
                    f"Ваше место доступа: <b>{slot} из {ACCESS_LIMIT}</b>",
                    f"Подключено VK-источников: <b>{len(self.sources)}</b>",
                    f"VK API токенов: <b>{self.vk.token_count}</b>",
                    f"Глубина поиска: <b>{self.lookback_hours} ч.</b>",
                    "",
                    "Команды: /check — проверить сейчас, /status — состояние, /pipeline — воронка.",
                ]
            ),
            reply_markup={"inline_keyboard": [[{"text": "Проверить сейчас", "callback_data": "check_now"}]]},
        )

    def process_message(self, message: Dict[str, Any]) -> None:
        text = str(message.get("text") or "").strip().lower()
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = str(chat.get("id", ""))
        chat_type = str(chat.get("type", ""))
        user_id = int(sender.get("id", 0) or 0)

        if text.startswith("/start"):
            if chat_type != "private":
                self.telegram.send_message(chat_id, "Первый запуск нужно сделать в личном чате с ботом.")
                return
            display_name = " ".join(filter(None, [str(sender.get("first_name", "")), str(sender.get("last_name", ""))]))
            result, slot = self.access.claim(
                user_id=user_id,
                chat_id=chat_id,
                username=str(sender.get("username", "")),
                display_name=display_name,
            )
            if result == "full":
                self.telegram.send_message(
                    chat_id,
                    "Доступ уже занят: бот закреплён за первыми двумя аккаунтами, которые нажали /start.",
                )
                return
            if result == "invalid":
                self.telegram.send_message(chat_id, "Не удалось определить Telegram-аккаунт.")
                return
            logging.info("Telegram-доступ: пользователь занял/подтвердил место %s", slot)
            self.welcome(chat_id, slot)
            return

        if text.startswith(("/check", "/status", "/pipeline", "/help")) and not self.authorized(message):
            self.telegram.send_message(chat_id, "Доступ есть только у первых двух аккаунтов, нажавших /start.")
            return

        if text.startswith("/status"):
            status = "идёт проверка" if self.scan_lock.locked() else "ожидает"
            self.telegram.send_message(
                chat_id,
                f"<b>Статус:</b> {status}\n<b>VK-источников:</b> {len(self.sources)}\n<b>VK API токенов:</b> {self.vk.token_count}\n<b>Доступов занято:</b> {self.access.count()}/{ACCESS_LIMIT}\n<b>Интервал:</b> {self.scan_interval // 60} мин.",
            )
            return
        if text.startswith("/pipeline"):
            self.telegram.send_message(chat_id, self.pipeline_text())
            return
        if text.startswith("/help"):
            self.welcome(chat_id, self.access.slot_for(user_id, chat_id))
            return
        if text.startswith("/check"):
            self.telegram.send_message(chat_id, "Принял. Проверяю свежие обращения по недвижимости ОАЭ в VK и Telegram…")

            def worker() -> None:
                result = self.run_scan("manual")
                self.telegram.send_message(chat_id, self.scan_result_text(result))

            threading.Thread(target=worker, daemon=True).start()

    def process_callback(self, callback: Dict[str, Any]) -> None:
        callback_id = str(callback.get("id", ""))
        message = callback.get("message") or {}
        pseudo_message = {"chat": message.get("chat") or {}, "from": callback.get("from") or {}}
        chat_id = str((message.get("chat") or {}).get("id", ""))
        message_id = int(message.get("message_id", 0) or 0)
        data = str(callback.get("data") or "")
        if not callback_id:
            return
        if not self.authorized(pseudo_message):
            self.telegram.answer_callback(callback_id, "Нет доступа")
            return

        if data.startswith("lead:"):
            parts = data.split(":", 2)
            if len(parts) != 3 or parts[1] not in {"work", "contact", "reject"}:
                self.telegram.answer_callback(callback_id, "Неизвестное действие")
                return
            status, lead_id = parts[1], parts[2]
            item = self.leads.update_status(lead_id, status)
            if not item:
                self.telegram.answer_callback(callback_id, "Лид уже вне истории")
                return
            status_names = {"work": "В работе", "contact": "Связались", "reject": "Не лид"}
            self.telegram.answer_callback(callback_id, f"Статус: {status_names[status]}")
            self.sheets.update_status(lead_id, status)
            if message_id and item.get("url"):
                self.telegram.edit_reply_markup(
                    chat_id,
                    message_id,
                    self.lead_keyboard(str(item["url"]), lead_id, current_status=status),
                )
            return

        if data != "check_now":
            return
        if self.scan_lock.locked():
            self.telegram.answer_callback(callback_id, "Проверка уже идёт")
            return
        self.telegram.answer_callback(callback_id, "Проверка запущена")
        self.telegram.send_message(chat_id, "Проверяю свежие обращения по недвижимости ОАЭ в VK и Telegram…")

        def worker() -> None:
            result = self.run_scan("manual_button")
            self.telegram.send_message(chat_id, self.scan_result_text(result))

        threading.Thread(target=worker, daemon=True).start()

    def scheduler_loop(self) -> None:
        while not self.stop_signal.is_set:
            if self.recipients():
                self.run_scan("scheduled")
            if self.stop_signal.wait(self.scan_interval):
                break

    def telegram_loop(self) -> None:
        while not self.stop_signal.is_set:
            try:
                for update in self.telegram.get_updates(timeout=25):
                    if "message" in update:
                        self.process_message(update["message"])
                    elif "callback_query" in update:
                        self.process_callback(update["callback_query"])
            except Exception:
                logging.exception("Ошибка обработки Telegram-команд")
                self.stop_signal.wait(5)

    def run(self) -> None:
        self.prepare(validate_telegram=True)
        self.telegram.request("deleteWebhook", {"drop_pending_updates": False})
        if self.recipients():
            self.broadcast(
                f"<b>🔥 Бот мониторинга лидов на недвижимость ОАЭ запущен</b>\nVK-источников: <b>{len(self.sources)}</b>\nTelegram-источников: <b>{self.telegram_source_count()}</b>.",
                reply_markup={"inline_keyboard": [[{"text": "Проверить сейчас", "callback_data": "check_now"}]]},
            )
        else:
            logging.info("Ожидается /start от первых двух Telegram-пользователей")
        scheduler = threading.Thread(target=self.scheduler_loop, daemon=True)
        scheduler.start()
        self.telegram_loop()
        scheduler.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Лиды на недвижимость ОАЭ: VK/Telegram → Telegram")
    parser.add_argument("--check-config", action="store_true", help="проверить токены и источники")
    parser.add_argument("--once", action="store_true", help="выполнить одну проверку")
    parser.add_argument("--dry-run", action="store_true", help="не отправлять найденные лиды")
    parser.add_argument("--sync-sheets", action="store_true", help="выгрузить сохранённые лиды в Google Sheets")
    args = parser.parse_args()

    setup_logging()
    monitor = Monitor()
    if args.sync_sheets:
        total, synced = monitor.sync_saved_leads_to_sheets()
        print(f"В таблицу выгружено лидов: {synced}/{total}")
        return
    if args.check_config:
        monitor.prepare(validate_telegram=True)
        print(f"OK: Telegram и VK проверены, источников подключено: {len(monitor.sources)}, VK API токенов: {monitor.vk.token_count}")
        return
    if args.once:
        monitor.prepare(validate_telegram=True)
        result = monitor.run_scan("once", dry_run=args.dry_run)
        print(f"Проверка завершена, результат: {result}")
        return
    monitor.run()


if __name__ == "__main__":
    main()
