import asyncio
import time
import schedule
import requests
from bs4 import BeautifulSoup  # ВИПРАВЛЕНО: Правильний імпорт BeautifulSoup
from urllib.parse import urljoin
import feedparser
from telegram import Bot
from telegram.error import TelegramError
import re
import os
import random
from thefuzz import fuzz
import pytz
from datetime import datetime

# --- КОНФІГУРАЦІЯ ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

# --- НАЛАШТУВАННЯ ДЖЕРЕЛ ---
SOURCES = [
    {"name": "ІТ (DOU.ua)", "rss_url": "https://dou.ua/lenta/articles/feed/", "base_url": "https://dou.ua/",
     "content_selectors": ["div.article-body", "div.b-typo"]},
    {"name": "Новини (Укр. Правда)", "rss_url": "https://www.pravda.com.ua/rss/",
     "base_url": "https://www.pravda.com.ua", "content_selectors": ["div.post_content"]},
    {"name": "ІТ (AIN.UA)", "rss_url": "https://ain.ua/feed/", "base_url": "https://ain.ua/",
     "content_selectors": ["div.post-content"]},
    {"name": "Війна (УНІАН)", "rss_url": "https://rss.unian.ua/war.rss", "base_url": "https://www.unian.ua/",
     "content_selectors": ["div.article-text"]},
    {"name": "ІТ (ITC.ua)", "rss_url": "https://itc.ua/ua/feed/", "base_url": "https://itc.ua/ua/",
     "content_selectors": ["div.entry-content"]}
]
AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
STATE_FILE = 'bot_state.txt'
GLOBAL_POSTED_TITLES_FILE = 'global_posted_titles.txt'
MAX_TITLES_TO_KEEP = 100
SIMILARITY_THRESHOLD = 85
KYIV_TZ = pytz.timezone('Europe/Kiev')


# --- Функції для роботи з глобальною пам'яттю ---
def get_recent_titles():
    try:
        with open(GLOBAL_POSTED_TITLES_FILE, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f]
    except FileNotFoundError:
        return []


def add_recent_title(title):
    titles = get_recent_titles()
    titles.append(title)
    with open(GLOBAL_POSTED_TITLES_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(titles[-MAX_TITLES_TO_KEEP:]))


def is_duplicate_title(new_title, recent_titles):
    for old_title in recent_titles:
        similarity = fuzz.ratio(new_title.lower(), old_title.lower())
        if similarity > SIMILARITY_THRESHOLD:
            print(f"Знайдено дублікат: '{new_title}' схожий на '{old_title}' ({similarity}%)")
            return True
    return False


# --- Функції для роботи з джерелами ---
def get_next_source_index():
    try:
        with open(STATE_FILE, 'r') as f:
            last_index = int(f.read().strip())
            return (last_index + 1) % len(SOURCES)
    except (FileNotFoundError, ValueError):
        return 0


def save_current_source_index(index):
    with open(STATE_FILE, 'w') as f:
        f.write(str(index))


def get_fallback_image():
    return "https://picsum.photos/1280/720"


def get_article_details(article_url, content_selectors):
    try:
        headers = {'User-Agent': AGENT}
        response = requests.get(article_url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        image_tag = soup.find('meta', property='og:image')
        image_url = image_tag['content'] if image_tag else None
        article_body = None
        for selector in content_selectors:
            article_body = soup.select_one(selector)
            if article_body:
                break
        if not article_body: return None, None
        for tag in article_body.find_all(['pre', 'code', 'script', 'style', 'aside']):
            tag.decompose()
        full_text = ' '.join(p.get_text(strip=True) for p in article_body.find_all('p'))
        sentences = re.split(r'(?<=[.!?])\s+', full_text)
        summary = ' '.join(sentences[:3]).strip()
        return image_url, summary if summary else None
    except Exception as e:
        print(f"Не вдалося отримати деталі статті {article_url}: {e}")
        return None, None


async def send_post_to_telegram(title, link, source_config):
    image_url, summary = get_article_details(link, source_config['content_selectors'])
    if not summary:
        print(f"Не вдалося отримати опис для статті: {title}. Пропускаю.")
        return False
    if not image_url:
        print("Реальне фото не знайдено. Беру випадкове зображення...")
        image_url = get_fallback_image()
    bot = Bot(BOT_TOKEN)
    caption = f"*{title}*\n\n{summary}"
    if len(caption) > 1024: caption = caption[:1020] + "..."
    try:
        if image_url:
            await bot.send_photo(chat_id=CHANNEL_ID, photo=image_url, caption=caption, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id=CHANNEL_ID, text=caption, parse_mode='Markdown')
        print(f"✅ Пост '{title}' успішно відправлено!")
        add_recent_title(title)
        return True
    except TelegramError as e:
        print(f"❌ Помилка Telegram: {e}")
        return False


# --- Основна логіка з новою перевіркою ---
async def post_news_from_source(source_config):
    """Знаходить унікальну статтю і повертає True у разі успішного постингу."""
    print(f"Перевіряю джерело: {source_config['name']} - {source_config['rss_url']}")
    try:
        headers = {'User-Agent': AGENT}
        response = requests.get(source_config['rss_url'], headers=headers, timeout=15)
        response.raise_for_status()
        feed = feedparser.parse(response.content)
        if not feed.entries:
            print("Стрічка новин порожня або не вдалося завантажити.")
            return False

        recent_titles = get_recent_titles()

        for entry in feed.entries:
            title = entry.title.strip()

            if not is_duplicate_title(title, recent_titles):
                print(f"Знайдено унікальну статтю: {title}")
                link = urljoin(source_config['base_url'], entry.link)
                if await send_post_to_telegram(title, link, source_config):
                    return True  # Повертаємо True, якщо пост відправлено

        print("Нових унікальних статей у цьому джерелі не знайдено.")
        return False
    except Exception as e:
        print(f"Сталася загальна помилка при обробці джерела {source_config['name']}: {e}")
        return False


async def main_task():
    """
    НОВА ЛОГІКА: Перебирає джерела по колу, доки не опублікує пост.
    """
    start_index = get_next_source_index()
    post_sent = False

    # Перебираємо всі джерела, починаючи з наступного за планом
    for i in range(len(SOURCES)):
        current_index = (start_index + i) % len(SOURCES)
        source_config = SOURCES[current_index]

        if await post_news_from_source(source_config):
            print(f"✅ Пост успішно відправлено з джерела: {source_config['name']}")
            save_current_source_index(current_index)  # Зберігаємо індекс успішного джерела
            post_sent = True
            break  # Виходимо з циклу, оскільки пост вже відправлено

    if not post_sent:
        print("❌ Не вдалося відправити пост з жодного джерела після перебору всіх.")


def job():
    current_hour = datetime.now(KYIV_TZ).hour
    if not (current_hour >= 8 or current_hour < 1):
        print(f"Зараз {current_hour}:00 (Київ). Постинг призупинено до 8 ранку.")
        return

    print(f"\n--- {datetime.now(KYIV_TZ).strftime('%Y-%m-%d %H:%M:%S')} ---")
    asyncio.run(main_task())


# --- ПЛАНУВАЛЬНИК ---
# Запускаємо завдання кожну годину.
schedule.every(1).hours.do(job)

print("🚀 Розумний бот-агрегатор запущений.")
print("Перша перевірка відбудеться негайно, якщо час відповідний (08:00 - 01:00).")
job()  # Перший запуск одразу

while True:
    schedule.run_pending()
    time.sleep(1)
