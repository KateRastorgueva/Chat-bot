import sqlite3
import hashlib
import time
import os
import re
from typing import Optional, List, Dict, Any
from enum import Enum
from openai import OpenAI
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from datetime import datetime
import pandas as pd
from classifier_service import IntentRouter
from curl_cffi import requests as curl_requests

class QuerySource(str, Enum):
    DB = "database"
    TUSUR_SITE = "tusur_site"
    TRUSTED_SITE = "trusted_site"
    OPERATOR = "operator"


class QueryResponse:
    def __init__(self, answer: str, source: QuerySource, confidence: float,
                 tags: List[str] = None, operator_notified: bool = False,
                 operator_ticket_id: Optional[str] = None):
        self.answer = answer
        self.source = source
        self.confidence = confidence
        self.tags = tags or []
        self.operator_notified = operator_notified
        self.operator_ticket_id = operator_ticket_id


class DatabaseConnector:
    def __init__(self, db_path: str = "university_data.db", excel_path: str = "FAQ.xlsx"):
        self.db_path = db_path
        self._init_db()
        self._import_excel_to_db(excel_path)

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS faq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT UNIQUE,
                answer TEXT,
                keywords TEXT
            )
        """)
        conn.commit()
        conn.close()

    def _import_excel_to_db(self, excel_path: str):
        if not os.path.exists(excel_path):
            print(f"Файл {excel_path} не найден")
            return

        try:
            df = pd.read_excel(excel_path)
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            for _, row in df.iterrows():
                if pd.notna(row.get('вопрос')) and pd.notna(row.get('ответ')):
                    question = str(row['вопрос']).strip()
                    answer = str(row['ответ']).strip()

                    if question and answer and question != "nan" and answer != "nan":
                        if question.startswith('Категория'):
                            continue

                        keywords = self._extract_keywords(question)

                        cursor.execute("""
                            INSERT OR REPLACE INTO faq (question, answer, keywords)
                            VALUES (?, ?, ?)
                        """, (question, answer, keywords))

            conn.commit()
            conn.close()
            print(f"Импортировано записей из FAQ.xlsx")
        except Exception as e:
            print(f"Ошибка импорта: {e}")

    def _extract_keywords(self, question: str) -> str:
        stop_words = ['как', 'что', 'когда', 'где', 'сколько', 'почему', 'зачем',
                      'какой', 'какая', 'какие', 'кого', 'кому', 'чем', 'это', 'то',
                      'такое', 'на', 'по', 'в', 'из', 'у', 'с', 'о', 'об', 'про',
                      'для', 'без', 'до', 'ли', 'же', 'бы', 'есть', 'нет', 'или', 'и']
        words = question.lower().split()
        keywords = [w for w in words if w not in stop_words and len(w) > 2]
        return ', '.join(keywords[:5])

    def search_in_db(self, query: str) -> Optional[str]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        query_lower = query.lower()

        stop_words = ['как', 'что', 'когда', 'где', 'сколько', 'почему', 'зачем',
                      'какой', 'какая', 'какие', 'кого', 'кому', 'чем', 'это', 'то',
                      'такое', 'на', 'по', 'в', 'из', 'у', 'с', 'о', 'об', 'про',
                      'для', 'без', 'до', 'ли', 'же', 'бы', 'есть', 'нет', 'или', 'и']

        query_keywords = [w for w in query_lower.split() if w not in stop_words and len(w) > 2]
        if not query_keywords:
            conn.close()
            return None

        cursor.execute("SELECT question, answer, keywords FROM faq")
        rows = cursor.fetchall()
        conn.close()

        best_score = 0.0
        best_answer = None
        q_set = set(query_keywords)

        for question, answer, keywords in rows:
            row_text = (question + " " + (keywords or "")).lower()
            row_words = set(w for w in row_text.split() if len(w) > 2)
            if not row_words:
                continue
            score = len(q_set & row_words) / len(q_set)
            if score > best_score:
                best_score = score
                best_answer = answer

        # Порог: минимум половина ключевых слов должна совпасть
        if best_score >= 0.5:
            return best_answer
        return None


class SiteParser:
    def __init__(self, name: str):
        self.name = name
        self.status_callback = None
        self._cache = {}

    def set_status_callback(self, callback):
        self.status_callback = callback

    def _update_status(self, message: str):
        if self.status_callback:
            self.status_callback(message)

    def _clean_text(self, soup: BeautifulSoup) -> tuple:
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        for tag in soup.find_all(["div", "section"], class_=re.compile(r"cookie|banner|alert|modal")):
            tag.decompose()

        for tag in soup.find_all(["div", "section", "nav", "ul"], class_=re.compile(r"menu|nav|breadcrumb|sidebar|header|footer")):
            tag.decompose()

        for tag in soup.find_all(["div", "section", "nav", "ul"], id=re.compile(r"menu|nav|breadcrumb|sidebar|header|footer")):
            tag.decompose()

        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        main = soup.find("main") or soup.find("article") or soup.find("body")
        if not main:
            return title, ""

        text_parts = []
        for tag in main.find_all(["h1", "h2", "h3", "p", "li"]):
            text = tag.get_text(strip=True)
            if text and len(text) > 20:
                text_parts.append(text[:500])

        content = "\n\n".join(text_parts[:40])
        return title, content

    def _get_page(self, url: str) -> Optional[tuple]:
        if url in self._cache:
            return self._cache[url]

        try:
            response = curl_requests.get(
                url,
                impersonate="chrome120",
                timeout=25
            )

            if response.status_code != 200:
                return None
            soup = BeautifulSoup(response.text, "html.parser")
            result = self._clean_text(soup)
            self._cache[url] = result
            return result
        except Exception as e:
            print(f"❌ ОШИБКА: {type(e).__name__}: {e}")
            return None

    def search_by_url(self, url: str) -> List[Dict[str, str]]:
        self._update_status(f"  → Загрузка: {url}")
        result = self._get_page(url)
        if result:
            title, content = result
            if content and len(content) > 100:
                return [{"url": url, "title": title, "content": content}]
        return []


class NeuralNetworkCore:
    def __init__(self, openrouter_key: str = None):
        self.db = DatabaseConnector()
        self.status_callback = None

        print("Инициализация классификатора...")
        self.router = IntentRouter(device="cpu")
        print("Классификатор готов")

        self.site_parser = SiteParser("сайт ТУСУРа")
        self.operator_tickets = {}

        print("Подключение к Ollama...")
        self.client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama"
        )
        self.model = "llama3.2"
        print("Ollama готов")

    def set_status_callback(self, callback):
        self.status_callback = callback
        self.site_parser.set_status_callback(callback)

    def _update_status(self, message: str):
        if self.status_callback:
            self.status_callback(message)

    def _generate_answer(self, query: str, context: str, source: str, definition: str = None) -> str:
        try:
            if len(context) > 5000:
                context = context[:5000]

            system_prompt = """Ты — ассистент поддержки студентов ТУСУРа. Отвечай на русском языке, кратко и по делу.

ЖЁСТКИЕ ПРАВИЛА:
1. Используй ТОЛЬКО информацию из блока "Контекст" и "Определение". Не выдумывай факты, контакты, ссылки, номера телефонов.
2. Если каких-то данных нет — просто НЕ упоминай их. Не пиши "нет информации", "[адрес]", "[телефон]" и подобные заглушки.
3. Не пересказывай правила из этого промпта в ответе.
4. Не дублируй определение из блока "Определение" — оно уже будет показано пользователю отдельно.
5. Если вопрос про действие ("как подать", "как получить") — дай пошаговую инструкцию. Если вопрос про факт — дай короткий прямой ответ.
6. Максимум 5 пунктов в инструкции. Каждый пункт — одно конкретное действие.
7. Объём ответа — до 1500 символов.
8. ЕСЛИ в контексте НЕТ прямого ответа на вопрос пользователя — верни РОВНО одну строку: NO_ANSWER

ФОРМАТ ОТВЕТА (выбирай только нужные блоки):
- Если вопрос про действие: пронумерованный список шагов.
- Если вопрос про факт: 1–3 предложения.
- Блок "Контакты:" добавляй ТОЛЬКО если в контексте есть телефон, email или адрес.
- Блок "Ссылки:" добавляй ТОЛЬКО если в контексте есть конкретные URL.
- Не используй эмодзи в заголовках, кроме случаев, когда они уже есть в контексте."""

            definition_block = ""
            if definition:
                definition_block = f"\n\nОпределение (уже показано пользователю, не повторяй):\n{definition}"

            user_prompt = f"""Вопрос пользователя: {query}

Источник информации: {source}{definition_block}

Контекст:
{context}

Дай ответ по правилам выше."""

            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.2,
                max_tokens=800
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"Ошибка: {str(e)}"

    def process_query(self, query: str, user_id: str = None) -> QueryResponse:
        # 1. Проверка на вопрос-определение ("что такое X")
        definition_patterns = ['что такое', 'кто такой', 'как расшифровывается',
                               'что значит', 'что означает']
        is_definition_question = any(pattern in query.lower() for pattern in definition_patterns)

        if is_definition_question:
            db_answer = self.db.search_in_db(query)
            if db_answer:
                self._update_status("✓ Найдено определение в БД!")
                return QueryResponse(
                    answer=db_answer,
                    source=QuerySource.DB,
                    confidence=0.95,
                    tags=["definition"]
                )

        # 2. Проверка на вопрос-процедуру ("как подать/поступить/получить")
        procedure_patterns = ['как подать', 'как поступить', 'как получить', 'как оформить',
                              'как записаться', 'как найти', 'как участвовать']
        is_procedure_question = any(pattern in query.lower() for pattern in procedure_patterns)

        # 3. Если это не процедурный вопрос — пробуем найти точный ответ в БД
        if not is_procedure_question:
            exact_answer = self.db.search_in_db(query)
            if exact_answer:
                self._update_status("✓ Найден точный ответ в БД!")
                return QueryResponse(
                    answer=exact_answer,
                    source=QuerySource.DB,
                    confidence=0.95,
                    tags=["faq"]
                )

        # 4. Классификация запроса
        self._update_status("ШАГ 1: Классификация запроса...")
        route_result = self.router.route(query, threshold=0.45)

        category = route_result.get("category")
        confidence = route_result.get("confidence", 0)
        urls = route_result.get("urls", [])

        term_definition = None

        if category:
            self._update_status(f"✓ Категория: {category} (уверенность: {confidence:.2f})")
            term_definition = self.db.search_in_db(category)
        else:
            self._update_status(f"✗ Категория не определена (уверенность: {confidence:.2f})")

        # 5. Перебор ВСЕХ URL: на каждом спрашиваем модель, есть ли ответ
        self._update_status("ШАГ 2: Поиск ответа на страницах...")

        if not urls:
            self._update_status("✗ Нет URL для загрузки")

        for i, url in enumerate(urls, 1):
            self._update_status(f"  → [{i}/{len(urls)}] {url}")
            pages = self.site_parser.search_by_url(url)

            if not pages:
                continue

            context = "\n\n".join([p["content"][:5000] for p in pages[:1]])
            answer = self._generate_answer(query, context, "сайта ТУСУРа", term_definition)

            if answer.strip() == "NO_ANSWER":
                self._update_status(f"  ✗ На странице нет ответа, пробую следующую...")
                continue

            # Нашли ответ — возвращаем
            self._update_status(f"✓ Ответ найден на странице {i}")
            return QueryResponse(
                answer=answer,
                source=QuerySource.TUSUR_SITE,
                confidence=confidence if category else 0.85,
                tags=[category] if category else ["tusur_site"]
            )

        self._update_status("✗ Ни на одной странице ответ не найден")

        # 6. Если есть определение из БД — отдаём его
        if term_definition:
            self._update_status("✓ Отвечаю на основе определения из БД")
            return QueryResponse(
                answer=term_definition,
                source=QuerySource.DB,
                confidence=0.8,
                tags=["definition_only"]
            )

        # 7. Передача оператору
        self._update_status("ШАГ 3: Передача оператору...")
        ticket_id = hashlib.md5(f"{query}{datetime.now()}".encode()).hexdigest()[:8]
        self.operator_tickets[ticket_id] = {
            "query": query,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat()
        }

        return QueryResponse(
            answer=f"К сожалению, я не смог найти точный ответ на ваш вопрос.\n\nНомер обращения: {ticket_id}\nОператор свяжется с вами в ближайшее время.",
            source=QuerySource.OPERATOR,
            confidence=0,
            operator_notified=True,
            operator_ticket_id=ticket_id
        )
