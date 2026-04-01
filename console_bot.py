import time
from main import NeuralNetworkCore, QuerySource

OPENROUTER_KEY = "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"

bot = NeuralNetworkCore(OPENROUTER_KEY)

start_time = None

def status_callback(message: str):
    global start_time
    if start_time is None:
        return
    elapsed = time.time() - start_time
    print(f"\r   [{elapsed:.0f}с] {message}", flush=True)

def print_response(response, elapsed_time):
    print("\n" + "=" * 60)
    source_names = {
        "database": "БАЗА ДАННЫХ",
        "vector": "ВЕКТОРНАЯ БАЗА",
        "university_site": "САЙТ ТУСУР",
        "internet": "ИНТЕРНЕТ",
        "operator": "ОПЕРАТОР"
    }
    source_name = source_names.get(response.source.value, response.source.value)
    print(f"ИСТОЧНИК: {source_name}")
    print(f"УВЕРЕННОСТЬ: {response.confidence * 100:.0f}%")
    print(f"ВРЕМЯ: {elapsed_time:.0f} сек")
    print("=" * 60)
    print(f"ОТВЕТ:\n{response.answer}")
    if response.operator_ticket_id:
        print(f"\nНомер обращения: {response.operator_ticket_id}")
    print("=" * 60)

def main():
    global start_time
    bot.set_status_callback(status_callback)
    
    print("=" * 60)
    print("ЧАТ-БОТ ПОДДЕРЖКИ ТУСУР")
    print("=" * 60)
    print("Команды: /exit - выход, 0 - выход, /clear - очистить экран")
    print("=" * 60)
    
    while True:
        print("\nВаш вопрос:", end=" ")
        user_input = input().strip()
        
        if user_input.lower() == "/exit" or user_input == "0":
            print("До свидания!")
            break
        
        if user_input.lower() == "/clear":
            print("\n" * 100)
            continue
        
        if not user_input:
            continue
        
        print("\n" + "-" * 40)
        start_time = time.time()
        
        try:
            response = bot.process_query(user_input, "console_user")
            elapsed = time.time() - start_time
            print_response(response, elapsed)
            
        except Exception as e:
            print(f"\n❌ Ошибка: {e}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nДо свидания!")
