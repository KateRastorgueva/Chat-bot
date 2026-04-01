import requests
import json
import subprocess
import sys
import os
import time
from typing import Dict, Any

class TestInterface:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.main_process = None
    
    def start_main_program(self, main_file_path: str):
        print(f"Запуск основной программы из: {main_file_path}")
        try:
            self.main_process = subprocess.Popen(
                [sys.executable, main_file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
            )
            time.sleep(3)
            print("Основная программа запущена")
            return True
        except Exception as e:
            print(f"Ошибка запуска: {e}")
            return False
    
    def stop_main_program(self):
        if self.main_process:
            self.main_process.terminate()
            self.main_process.wait()
            print("Основная программа остановлена")
    
    def send_query(self, query: str, user_id: str = None) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.base_url}/process",
                json={"query": query, "user_id": user_id},
                timeout=30
            )
            return response.json()
        except requests.exceptions.ConnectionError:
            return {"error": "Основная программа не запущена"}
        except Exception as e:
            return {"error": str(e)}
    
    def add_db_entry(self, question: str, answer: str, keywords: str, tags: str) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.base_url}/admin/add_db_entry",
                params={
                    "question": question,
                    "answer": answer,
                    "keywords": keywords,
                    "tags": tags
                }
            )
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def index_vector_data(self, data_path: str) -> Dict[str, Any]:
        try:
            response = requests.post(
                f"{self.base_url}/admin/index_vector_data",
                params={"data_path": data_path}
            )
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def get_tickets(self) -> Dict[str, Any]:
        try:
            response = requests.get(f"{self.base_url}/admin/tickets")
            return response.json()
        except Exception as e:
            return {"error": str(e)}
    
    def health_check(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except:
            return False
    
    def run_interactive_test(self, main_file_path: str):
        print("=" * 70)
        print("ТЕСТОВЫЙ ИНТЕРФЕЙС НЕЙРОСЕТИ ПОДДЕРЖКИ УНИВЕРСИТЕТА")
        print("=" * 70)
        
        print("\nПроверка доступности основной программы...")
        if not self.health_check():
            print("Основная программа не запущена")
            choice = input("Запустить основную программу? (y/n): ").lower()
            if choice == 'y':
                if not self.start_main_program(main_file_path):
                    print("Не удалось запустить программу")
                    return
            else:
                print("Невозможно продолжить без основной программы")
                return
        
        print("\nДоступные команды:")
        print("-" * 70)
        
        while True:
            print("\n" + "=" * 70)
            print("ГЛАВНОЕ МЕНЮ")
            print("=" * 70)
            print("1. Отправить запрос нейросети")
            print("2. Добавить запись в базу данных")
            print("3. Индексировать векторные данные")
            print("4. Просмотреть обращения к оператору")
            print("5. Перезапустить основную программу")
            print("6. Проверить статус")
            print("7. Выйти")
            
            choice = input("\nВаш выбор (1-7): ").strip()
            
            if choice == "1":
                print("\n" + "-" * 70)
                print("ОТПРАВКА ЗАПРОСА")
                print("-" * 70)
                query = input("Введите ваш вопрос: ")
                print("\nОбработка запроса...")
                result = self.send_query(query, "test_user_001")
                
                print("\n" + "=" * 70)
                print("РЕЗУЛЬТАТ")
                print("=" * 70)
                if "error" in result:
                    print(f"❌ Ошибка: {result['error']}")
                else:
                    print(f"📝 Вопрос: {query}")
                    print(f"🤖 Ответ: {result['answer']}")
                    print(f"📚 Источник: {result['source']}")
                    print(f"📊 Уверенность: {result['confidence'] * 100:.0f}%")
                    if result.get('tags'):
                        print(f"🏷️ Теги: {', '.join(result['tags'])}")
                    if result.get('operator_ticket_id'):
                        print(f"🎫 Номер обращения: {result['operator_ticket_id']}")
                print("=" * 70)
            
            elif choice == "2":
                print("\n" + "-" * 70)
                print("ДОБАВЛЕНИЕ ЗАПИСИ В БАЗУ ДАННЫХ")
                print("-" * 70)
                question = input("Вопрос: ")
                answer = input("Ответ: ")
                keywords = input("Ключевые слова (через запятую): ")
                tags = input("Теги (через запятую): ")
                result = self.add_db_entry(question, answer, keywords, tags)
                if "error" in result:
                    print(f"❌ Ошибка: {result['error']}")
                else:
                    print(f"✅ {result['status']}: {result['question']}")
            
            elif choice == "3":
                print("\n" + "-" * 70)
                print("ИНДЕКСАЦИЯ ВЕКТОРНЫХ ДАННЫХ")
                print("-" * 70)
                default_path = os.path.join(os.path.dirname(__file__), "university_data")
                path = input(f"Путь к папке с файлами [{default_path}]: ").strip()
                if not path:
                    path = default_path
                print(f"Индексация данных из {path}...")
                result = self.index_vector_data(path)
                if "error" in result:
                    print(f"❌ Ошибка: {result['error']}")
                else:
                    print(f"✅ {result['status']}: {result['path']}")
            
            elif choice == "4":
                print("\n" + "-" * 70)
                print("ОБРАЩЕНИЯ К ОПЕРАТОРУ")
                print("-" * 70)
                tickets = self.get_tickets()
                if "error" in tickets:
                    print(f"❌ Ошибка: {tickets['error']}")
                else:
                    tickets_list = tickets.get('tickets', {})
                    if not tickets_list:
                        print("Нет активных обращений")
                    else:
                        print(f"Всего обращений: {len(tickets_list)}")
                        for ticket_id, data in tickets_list.items():
                            print(f"\n🎫 {ticket_id}")
                            print(f"   Вопрос: {data['query']}")
                            print(f"   Пользователь: {data.get('user_id', 'не указан')}")
                            print(f"   Время: {data['timestamp']}")
            
            elif choice == "5":
                print("\nПерезапуск основной программы...")
                self.stop_main_program()
                time.sleep(2)
                self.start_main_program(main_file_path)
                time.sleep(3)
                if self.health_check():
                    print("✅ Программа успешно перезапущена")
                else:
                    print("❌ Не удалось перезапустить программу")
            
            elif choice == "6":
                print("\n" + "-" * 70)
                print("СТАТУС СИСТЕМЫ")
                print("-" * 70)
                if self.health_check():
                    print("✅ Основная программа: РАБОТАЕТ")
                    print(f"📍 API: {self.base_url}")
                    print(f"📖 Документация: {self.base_url}/docs")
                else:
                    print("❌ Основная программа: НЕ ДОСТУПНА")
            
            elif choice == "7":
                print("\nЗавершение работы...")
                choice_stop = input("Остановить основную программу? (y/n): ").lower()
                if choice_stop == 'y':
                    self.stop_main_program()
                print("До свидания!")
                break
            
            else:
                print("❌ Неверный выбор")

if __name__ == "__main__":
    main_file_path = r"C:\Users\rast6\OneDrive\Рабочий стол\чат бот\main.py"
    
    if not os.path.exists(main_file_path):
        print(f"❌ Файл основной программы не найден: {main_file_path}")
        print("Укажите правильный путь к файлу main.py")
        new_path = input("Введите правильный путь: ").strip()
        if new_path:
            main_file_path = new_path
        else:
            sys.exit(1)
    
    interface = TestInterface()
    
    try:
        interface.run_interactive_test(main_file_path)
    except KeyboardInterrupt:
        print("\n\nПрерывание работы")
        interface.stop_main_program()
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        interface.stop_main_program()
