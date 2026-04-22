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
        
        conditions = []
        params = []
        for kw in query_keywords:
            conditions.append("(LOWER(question) LIKE ? OR LOWER(keywords) LIKE ?)")
            params.append(f"%{kw}%")
            params.append(f"%{kw}%")
        
        sql = f"""
            SELECT question, answer FROM faq 
            WHERE {' OR '.join(conditions)}
            ORDER BY 
                CASE 
                    WHEN LOWER(question) = ? THEN 1
                    WHEN LOWER(question) LIKE ? THEN 2
                    ELSE 3
                END
            LIMIT 1
        """
        params.append(query_lower)
        params.append(f"%{query_lower}%")
        
        cursor.execute(sql, params)
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return result[1]
        return None

class SiteParser:
    def __init__(self, urls: List[str], name: str):
        self.urls = urls
        self.name = name
        self.status_callback = None
        
        self.term_to_url = {
            "гпо": "gruppovoe-proektnoe-obuchenie",
            "стипендия": "stipendii",
            "общежитие": "obshchezhitiya",
            "расписание": "raspisanie",
            "аспирантура": "postgraduate",
            "магистратура": "magistratura",
            "поступление": "abitur",
            "библиотека": "lib",
            "спорт": "sport",
            "профком": "profkom",
            "практика": "praktika",
            "трудоустройство": "trudoustroystvo",
            "фвс": "fvs",
            "ксуп": "ksup"
        }
    
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
    
    def get_url_by_term(self, term: str) -> Optional[str]:
        term_lower = term.lower()
        if term_lower in self.term_to_url:
            url_part = self.term_to_url[term_lower]
            for url in self.urls:
                if url_part in url:
                    return url
        return None
    
    def search(self, query: str, target_url: str = None, max_pages: int = 40) -> List[Dict[str, str]]:
        query_lower = query.lower()
        query_keywords = [w for w in query_lower.split() if len(w) > 3][:5]
        
        if target_url:
            self._update_status(f"  → Целевой поиск по URL: {target_url}")
            result = self._get_page(target_url)
            if result:
                title, content = result
                if content:
                    return [{"url": target_url, "title": title, "content": content}]
            return []
        
        pages = []
        for url in self.urls[:max_pages]:
            result = self._get_page(url)
            if result:
                title, content = result
                if content:
                    content_lower = content.lower()
                    if any(kw in content_lower for kw in query_keywords):
                        pages.append({"url": url, "title": title, "content": content})
                        if len(pages) >= 3:
                            break
            time.sleep(0.2)
        
        return pages

class NeuralNetworkCore:
    def __init__(self, openrouter_key: str):
        self.db = DatabaseConnector()
        self.status_callback = None
        
        tusur_urls = [
            "https://tusur.ru",
            "https://tusur.ru/ru/studentam",
            "https://tusur.ru/ru/studentam/gruppovoe-proektnoe-obuchenie",
            "https://tusur.ru/ru/abitur",
            "https://tusur.ru/ru/studentam/stipendii",
            "https://tusur.ru/ru/studentam/stipendii-i-materialnaya-pomosch",
            "https://tusur.ru/ru/studentam/obshchezhitiya",
            "https://tusur.ru/ru/studentam/raspisanie",
            "https://tusur.ru/ru/studentam/nauchnaya-rabota",
            "https://tusur.ru/ru/nauka-i-innovatsii/konferentsii",
            "https://tusur.ru/ru/novosti-i-meropriyatiya",
            "https://tusur.ru/ru/novosti-i-meropriyatiya/novosti",
            "https://tusur.ru/ru/o-tusure",
            "https://tusur.ru/ru/o-tusure/kontakty-i-rekvizity",
            "https://tusur.ru/ru/o-tusure/telefonnyy-spravochnik",
            "https://tusur.ru/ru/o-tusure/partnyory",
            "https://tusur.ru/ru/sotrudnikam",
            "https://tusur.ru/ru/education",
            "https://tusur.ru/ru/education/dopolnitelnoe-obrazovanie",
            "https://tusur.ru/ru/education/aspirantura",
            "https://tusur.ru/ru/studentam/praktika",
            "https://tusur.ru/ru/studentam/praktiki",
            "https://tusur.ru/ru/studentam/trudoustroystvo",
            "https://tusur.ru/ru/studentam/sport",
            "https://tusur.ru/ru/studentam/profkom",
            "https://tusur.ru/ru/studentam/obschestvennaya-zhizn",
            "https://tusur.ru/ru/studentam/distantsionnoe-obuchenie",
            "https://tusur.ru/ru/studentam/vypusknikam",
            "https://tusur.ru/ru/nauka-i-innovatsii",
            "https://tusur.ru/ru/sotrudnichestvo",
            "https://tusur.ru/ru/sotrudnichestvo/mezhdunarodnaya-deyatelnost",
            "https://tusur.ru/ru/studentam/pravila-obucheniya",
            "https://tusur.ru/ru/studentam/oplata-obucheniya-i-platnye-uslugi",
            "https://tusur.ru/ru/studentam/kuratory",
            "https://tusur.ru/ru/studentam/spravka-s-mesta-ucheby",
            "https://tusur.ru/ru/studentam/gosudarstvennaya-itogovaya-attestatsiya-gia",
            "https://tusur.ru/ru/studentam/internet-ekzameny-dlya-studentov-1-i-4-kursa",
            "https://tusur.ru/ru/studentam/fakultativy-i-elektivy",
            "https://tusur.ru/ru/studentam/tselevoe-obuchenie",
            "https://tusur.ru/ru/studentam/uchebnye-i-drugie-korpusa",
            "https://tusur.ru/ru/studentam/zhizn-v-obschezhitii",
            "https://tusur.ru/ru/studentam/konkursy-i-konferentsii",
            "https://tusur.ru/ru/obrazovanie/olimpiady",
            "https://tusur.ru/ru/studentam/issledovaniya-i-biznes",
            "https://tusur.ru/ru/studentam/studotryady",
            "https://tusur.ru/ru/studentam/kluby-po-interesam-i-volonterstvo",
            "https://tusur.ru/ru/studentam/pervichnaya-profsoyuznaya-organizatsiya-studentov-tusur",
            "https://tusur.ru/ru/studentam/ob-edinennyy-sovet-obuchayuschihsya-tusura",
            "https://tusur.ru/ru/nauka-i-innovatsii/studencheskoe-nauchnoe-soobschestvo-sistema",
            "https://tusur.ru/ru/sotrudnichestvo/obrazovatelnoe-sotrudnichestvo#traineeship",
            "https://tusur.ru/ru/o-tusure/struktura-i-organy-upravleniya/departament-obrazovaniya/fakultety-i-kafedry/gumanitarnyy-fakultet/kafedra-inostrannyh-yazykov",
            "https://freshman.tusur.ru",
            "https://timetable.tusur.ru",
            "https://edu.tusur.ru",
            "https://priem.tusur.ru",
            "https://abiturient.tusur.ru",
            "https://postgraduate.tusur.ru",
            "https://avt.tusur.ru",
            "https://lib.tusur.ru",
            "https://attendance.tusur.ru",
            "https://online.tusur.ru",
            "https://directory.tusur.ru",
            "https://profile.tusur.ru",
            "https://profile.tusur.ru/question",
            "https://profile.tusur.ru/portfolio",
            "https://sbi.tusur.ru",
            "https://cloud.tusur.ru",
            "https://tusur-courses.ru",
            "https://sport.tusur.ru",
            "https://vk.com/centercareer_tusur"
        ]
        
        trusted_urls = [
            "https://minobrnauki.gov.ru",
            "https://minobrnauki.gov.ru/about/gosudarstvennye-nauchnye-tsentry-rossiyskoy-federatsii",
            "https://minobrnauki.gov.ru/contacts",
            "https://minobrnauki.gov.ru/colleges_councils/kollegialnye-organy/digitalcouncil",
            "https://minobrnauki.gov.ru/colleges_councils/kollegialnye-organy/youthpolicy_council",
            "https://minobrnauki.gov.ru/action/targeted_training",
            "https://minobrnauki.gov.ru/action/young_scientists",
            "https://www.consultant.ru",
            "https://www.consultant.ru/document/cons_doc_LAW_140174",
            "https://do.tomsk.gov.ru",
            "https://do.tomsk.gov.ru/podtverzhdenie-dokumentov-ob-obrazovanii-i-ili-o-kvalifikatsii-ob-uchenyh-stepenjah-uchenyh-zvanijah-apostil",
            "https://do.tomsk.gov.ru/obrazovatelno-otraslevye-klastery",
            "https://do.tomsk.gov.ru/professionalitet",
            "https://do.tomsk.gov.ru/peredannye-polnomochija-tomskobrnadzor",
            "https://do.tomsk.gov.ru/dokumenty",
            "https://do.tomsk.gov.ru/news/front",
            "https://do.tomsk.gov.ru/phonebook/department",
            "https://xn--d1amqcgedd.xn--p1ai",
            "https://xn--d1amqcgedd.xn--p1ai/o_rossiyskikh_studencheskikh_otryadakh",
            "https://xn--d1amqcgedd.xn--p1ai/deyatelnost/meropriyatiya",
            "https://xn--d1amqcgedd.xn--p1ai/deyatelnost/sotsialnye_proekty",
            "https://xn--d1amqcgedd.xn--p1ai/dokumenty",
            "https://xn--d1amqcgedd.xn--p1ai/novosti",
            "https://xn--d1amqcgedd.xn--p1ai/press_sluzhba/kontakt",
            "https://xn--d1amqcgedd.xn--p1ai/bud_s_nami/khochu_v_studencheskiy_otryad",
            "https://xn--d1amqcgedd.xn--p1ai/kontakty/map/sfo/tomskaya-oblast",
            "https://xn--d1amqcgedd.xn--p1ai/profobuchenie_studencheskikh_otryadov",
            "https://obrnadzor.gov.ru",
            "https://www.gosuslugi.ru/vuznavigator",
            "https://www.gosuslugi.ru/vuzonline",
            "https://studyinrussia.ru",
            "https://education-in-russia.com",
            "https://xn--80aaajkjea9ajidh2c8f.xn--p1ai",
            "https://rsv.ru",
            "https://www.ras.ru",
            "https://zakupki.gov.ru",
            "https://isga.obrnadzor.gov.ru",
            "https://monitoring.miccedu.ru",
            "https://rsr-olymp.ru",
            "https://programs.economy.gov.ru",
            "https://cyberleninka.ru",
            "https://elibrary.ru",
            "http://pravo.gov.ru",
            "https://garant.ru"
        ]
        
        self.tusur_parser = SiteParser(tusur_urls, "сайта ТУСУРа")
        self.trusted_parser = SiteParser(trusted_urls, "проверенных сайтов")
        self.operator_tickets = {}
        
        self.client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_key
        )
        self.model = "openai/gpt-3.5-turbo"
    
    def set_status_callback(self, callback):
        self.status_callback = callback
        self.tusur_parser.set_status_callback(callback)
        self.trusted_parser.set_status_callback(callback)
    
    def _update_status(self, message: str):
        if self.status_callback:
            self.status_callback(message)
    def _generate_answer(self, query: str, context: str, source: str, definition: str = None) -> str:
        try:
            if len(context) > 5000:
                context = context[:5000]
            
            definition_part = f"\n\n📚 **Определение из базы знаний:**\n{definition}" if definition else ""
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": """Ты — интеллектуальный помощник студентов ТУСУРа.

**УНИВЕРСАЛЬНЫЙ АЛГОРИТМ ОТВЕТА:**

1. Если в запросе есть определение термина — начни ответ с него
2. Затем дай полный ответ на вопрос пользователя, используя информацию с сайта
3. Если вопрос о процедуре (как подать, как поступить, как получить) — дай пошаговую инструкцию с нумерацией
4. Если есть контакты (телефон, кабинет, email) — обязательно укажи
5. Если есть сроки, даты — выдели их
6. Если есть ссылки на документы или страницы — приведи

**ФОРМАТ ИДЕАЛЬНОГО ОТВЕТА (ШАБЛОН):**

📚 **Определение:**
[Краткое определение термина из базы знаний]

**Основной ответ на вопрос:**
[Развёрнутый ответ на основе информации с сайта]

**Пошаговая инструкция:**
1. **Шаг первый** — [конкретное действие]
2. **Шаг второй** — [конкретное действие]
3. **Шаг третий** — [конкретное действие]

📌 **Контакты:**
- [Название отдела/кафедры]: [адрес]
- Телефон: [номер]
- Режим работы: [дни и часы]

🔗 **Полезные ссылки:**
- [Название страницы]: [URL]

**ПРАВИЛА:**
- Не подменяй понятия — отвечай строго по теме вопроса
- Будь полезным и информативным
- Используй нумерованные списки для шагов
- Выделяй важное **жирным** или 📌
- Если информации недостаточно, честно скажи об этом"""},
                    {"role": "user", "content": f"Вопрос: {query}\n\n{definition_part}\n\nИнформация с {source}:\n{context}\n\nДай полный ответ по шаблону выше."}
                ],
                temperature=0.4,
                max_tokens=1000
            )
            return response.choices[0].message.content
        except Exception as e:
            return f"Ошибка: {str(e)}"
    def process_query(self, query: str, user_id: str = None) -> QueryResponse:
        definition_patterns = ['что такое', 'кто такой', 'как расшифровывается', 'что значит', 'что означает']
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
        
        procedure_patterns = ['как подать', 'как поступить', 'как получить', 'как оформить', 'как записаться', 'как найти', 'как участвовать']
        is_procedure_question = any(pattern in query.lower() for pattern in procedure_patterns)
        
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
        term = None
        terms_list = ['гпо', 'стипендия', 'общежитие', 'расписание', 'аспирантура', 'магистратура', 'фвс', 'ксуп', 'поступление', 'библиотека', 'спорт', 'профком', 'практика', 'трудоустройство']
        for t in terms_list:
            if t in query.lower():
                term = t
                break
        
        term_definition = None
        target_url = None
        
        if term:
            self._update_status(f"ШАГ 1: Поиск определения термина '{term}' в БД...")
            term_definition = self.db.search_in_db(term)
            if term_definition:
                self._update_status(f"✓ Определение термина '{term}' найдено")
            
            target_url = self.tusur_parser.get_url_by_term(term)
            if target_url:
                self._update_status(f"  → Найдена целевая страница: {target_url}")
        
        self._update_status("ШАГ 2: Поиск на сайте ТУСУРа...")
        

        if target_url:
            tusur_pages = self.tusur_parser.search(query, target_url=target_url)
        else:
            tusur_pages = self.tusur_parser.search(query, target_url=None)
        
        if tusur_pages:
            self._update_status(f"✓ Найдено {len(tusur_pages)} страниц на сайте ТУСУРа")
            context = "\n\n".join([p["content"][:1500] for p in tusur_pages[:2]])
            answer = self._generate_answer(query, context, "сайта ТУСУРа", term_definition)
            return QueryResponse(
                answer=answer,
                source=QuerySource.TUSUR_SITE,
                confidence=0.85,
                tags=["tusur_site"]
            )
        self._update_status("✗ На сайте ТУСУРа не найдено")
        
        self._update_status("ШАГ 3: Поиск на проверенных сайтах...")
        trusted_pages = self.trusted_parser.search(query, target_url=None)
        
        if trusted_pages:
            self._update_status(f"✓ Найдено {len(trusted_pages)} страниц на проверенных сайтах")
            context = "\n\n".join([p["content"][:1500] for p in trusted_pages[:2]])
            answer = self._generate_answer(query, context, "проверенных сайтов", term_definition)
            return QueryResponse(
                answer=answer,
                source=QuerySource.TRUSTED_SITE,
                confidence=0.75,
                tags=["trusted_site"]
            )
        self._update_status("✗ На проверенных сайтах не найдено")
        
        if term_definition:
            self._update_status("✓ Отвечаю на основе определения из БД")
            return QueryResponse(
                answer=term_definition,
                source=QuerySource.DB,
                confidence=0.8,
                tags=["definition_only"]
            )
        
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
