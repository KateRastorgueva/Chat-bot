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

class QuerySource(str, Enum):
    DB = "database"
    UNIVERSITY_SITE = "university_site"
    INTERNET = "internet"
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
    def __init__(self, db_path: str = "university_data.db"):
        self.db_path = db_path
        self._init_db()
    
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
    
    def search_faq(self, query: str) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        query_lower = query.lower()
        cursor.execute("""
            SELECT question, answer FROM faq 
            WHERE LOWER(question) LIKE ? OR LOWER(keywords) LIKE ?
            LIMIT 1
        """, (f"%{query_lower}%", f"%{query_lower}%"))
        result = cursor.fetchone()
        conn.close()
        if result:
            return {"answer": result[1], "confidence": 0.95}
        return None
    
    def add_faq(self, question: str, answer: str, keywords: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO faq (question, answer, keywords) VALUES (?, ?, ?)",
            (question, answer, keywords)
        )
        conn.commit()
        conn.close()

class UniversitySiteParser:
    def __init__(self):
        self.base_url = "https://tusur.ru"
        self.status_callback = None
    
    def set_status_callback(self, callback):
        self.status_callback = callback
    
    def _update_status(self, message: str):
        if self.status_callback:
            self.status_callback(message)
    
    def _clean_text(self, soup: BeautifulSoup) -> tuple:
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        
        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else ""
        
        main = soup.find("main") or soup.find("article") or soup.find("body")
        if not main:
            return title, ""
        
        text_parts = []
        for tag in main.find_all(["h1", "h2", "h3", "p", "li"]):
            text = tag.get_text(strip=True)
            if text and len(text) > 30:
                text_parts.append(text[:500])
        
        content = "\n\n".join(text_parts[:20])
        return title, content
    
    def _get_page(self, url: str) -> Optional[tuple]:
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code != 200:
                return None
            soup = BeautifulSoup(response.text, "html.parser")
            return self._clean_text(soup)
        except:
            return None
    
    def search(self, query: str) -> List[Dict[str, str]]:
        query_lower = query.lower()
        
        topic_urls = {
            "гпо": ["https://tusur.ru/ru/studentam/gruppovoe-proektnoe-obuchenie"],
            "расписание": ["https://timetable.tusur.ru"],
            "стипендия": ["https://tusur.ru/ru/studentam/stipendii"],
            "общежитие": ["https://tusur.ru/ru/studentam/obshchezhitiya"],
            "поступление": ["https://priem.tusur.ru", "https://abiturient.tusur.ru"],
            "научная сессия": ["https://tusur.ru/ru/nauka-i-innovatsii/konferentsii", "https://tusur.ru/ru/novosti-i-meropriyatiya"],
            "конференция": ["https://tusur.ru/ru/nauka-i-innovatsii/konferentsii"],
            "аспирантура": ["https://postgraduate.tusur.ru"],
            "выпускники": ["https://avt.tusur.ru"],
            "библиотека": ["https://lib.tusur.ru"],
            "спорт": ["https://tusur.ru/ru/studentam/sport"],
            "профком": ["https://tusur.ru/ru/studentam/profkom"],
            "практика": ["https://tusur.ru/ru/studentam/praktika"],
            "трудоустройство": ["https://tusur.ru/ru/studentam/trudoustroystvo"]
        }
        
        urls_to_check = []
        for keyword, urls in topic_urls.items():
            if keyword in query_lower:
                urls_to_check = urls
                break
        
        if not urls_to_check:
            urls_to_check = [
                "https://tusur.ru/ru/studentam",
                "https://tusur.ru/ru/abitur"
            ]
        
        pages = []
        for url in urls_to_check[:5]:
            result = self._get_page(url)
            if result:
                title, content = result
                if content:
                    pages.append({"url": url, "title": title, "content": content})
                    if len(pages) >= 2:
                        break
            time.sleep(0.2)
        
        return pages

class InternetSearch:
    def __init__(self):
        self.status_callback = None
    
    def set_status_callback(self, callback):
        self.status_callback = callback
    
    def _update_status(self, message: str):
        if self.status_callback:
            self.status_callback(message)
    
    def search(self, query: str) -> Optional[str]:
        self._update_status("Ищу в интернете...")
        try:
            response = requests.get(
                f"https://www.google.com/search?q=site:tusur.ru+{query}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10
            )
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                results = []
                for item in soup.find_all("div", class_="g")[:3]:
                    title = item.find("h3")
                    snippet = item.find("div", class_="VwiC3b")
                    if title and snippet:
                        results.append(f"{title.get_text()}\n{snippet.get_text()}")
                return "\n\n".join(results) if results else None
        except:
            pass
        return None

class NeuralNetworkCore:
    def __init__(self, openrouter_key: str):
        self.db = DatabaseConnector()
        self.site_parser = UniversitySiteParser()
        self.internet = InternetSearch()
        self.operator_tickets = {}
        self.status_callback = None
        
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key
        )
        self.model = "openai/gpt-3.5-turbo"
    
    def set_status_callback(self, callback):
        self.status_callback = callback
        self.site_parser.set_status_callback(callback)
        self.internet.set_status_callback(callback)
    
    def _update_status(self, message: str):
        if self.status_callback:
            self.status_callback(message)
    
    def _generate_answer(self, query: str, context: str, source: str) -> str:
        try:
            if len(context) > 5000:
                context = context[:5000]
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": """Ты — интеллектуальный помощник студентов ТУСУРа. Твоя задача — давать РАЗВЕРНУТЫЕ, ПОЛНЫЕ и ТОЧНЫЕ ответы.

УНИВЕРСАЛЬНЫЕ ПРАВИЛА:
1. Внимательно прочитай вопрос и определи, о чём именно спрашивают
2. Найди в предоставленной информации ответ, который соответствует теме вопроса
3. Не подменяй понятия — если спрашивают об одном, не отвечай о другом
4. Если в тексте есть конкретные шаги, инструкции, контакты, сроки — обязательно их укажи
5. Структурируй ответ: начни с краткого введения, затем опиши действия, укажи контакты и ссылки
6. Будь полезным и информативным

Формат ответа:
- Краткое введение по теме
- Пошаговые инструкции (если есть)
- Контактные данные (если есть)
- Важные примечания и дополнительные ссылки"""},
                    {"role": "user", "content": f"Вопрос: {query}\n\nИнформация с {source}:\n{context}\n\nДай РАЗВЕРНУТЫЙ, ПОЛНЫЙ ответ."}
                ],
                temperature=0.4,
                max_tokens=1000
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Ошибка: {str(e)}"
    
    def process_query(self, query: str, user_id: str = None) -> QueryResponse:
        self._update_status("ШАГ 1: Поиск в БД...")
        db_result = self.db.search_faq(query)
        if db_result:
            self._update_status("✓ Найдено в БД!")
            return QueryResponse(
                answer=db_result["answer"],
                source=QuerySource.DB,
                confidence=db_result["confidence"],
                tags=["faq"]
            )
        self._update_status("✗ В БД не найдено")
        
        self._update_status("ШАГ 2: Поиск на сайте ТУСУРа...")
        pages = self.site_parser.search(query)
        
        if pages:
            self._update_status(f"✓ Найдено {len(pages)} страниц")
            context = "\n\n".join([p["content"][:1500] for p in pages[:2]])
            answer = self._generate_answer(query, context, "сайта")
            return QueryResponse(
                answer=answer,
                source=QuerySource.UNIVERSITY_SITE,
                confidence=0.85,
                tags=["university_site"]
            )
        
        self._update_status("ШАГ 3: Поиск в интернете...")
        internet_content = self.internet.search(query)
        if internet_content:
            self._update_status("✓ Найдено в интернете")
            answer = self._generate_answer(query, internet_content, "интернета")
            return QueryResponse(
                answer=answer,
                source=QuerySource.INTERNET,
                confidence=0.6,
                tags=["internet"]
            )
        self._update_status("✗ В интернете не найдено")
        
        self._update_status("ШАГ 4: Передача оператору...")
        ticket_id = hashlib.md5(f"{query}{datetime.now()}".encode()).hexdigest()[:8]
        self.operator_tickets[ticket_id] = {
            "query": query,
            "user_id": user_id,
            "timestamp": datetime.now().isoformat()
        }
        
        return QueryResponse(
            answer=f"К сожалению, я не смог найти ответ на ваш вопрос.\n\nНомер обращения: {ticket_id}\nОператор свяжется с вами в ближайшее время.",
            source=QuerySource.OPERATOR,
            confidence=0,
            operator_notified=True,
            operator_ticket_id=ticket_id
        )
