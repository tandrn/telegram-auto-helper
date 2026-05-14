import json
import asyncio
import random
import os
from dotenv import load_dotenv

# Подгружаем переменные из .env (ключи Яндекса)
load_dotenv()

# Импортируем функцию анализа стиля из твоего же проекта
from src.llm_client import analyze_style

async def build_profile():
    file_path = "cleaned_style.json"
    
    if not os.path.exists(file_path):
        print(f"❌ Файл {file_path} не найден! Положи его рядом со скриптом.")
        return

    # 1. Читаем очищенные диалоги
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not data:
        print("❌ Файл пуст!")
        return

    # 2. Выбираем 30 случайных пар (если скормить LLM все 5000, она сойдет с ума от переизбытка токенов)
    samples = random.sample(data, min(80, len(data)))
    
    # 3. Форматируем для передачи в LLM
    lines_for_llm = []
    for item in samples:
        line = f"Собеседник: {item['context']}\nЯ: {item['my_response']}"
        lines_for_llm.append(line)

    print(f"⏳ Отправляем {len(lines_for_llm)} примеров диалогов в Yandex AI для анализа...")
    
    try:
        # 4. Вызываем LLM напрямую (без Телеграма)
        profile = await analyze_style(lines_for_llm)
        
        # 5. Сохраняем профиль
        with open("style_profile.json", "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
            
        print("✅ УСПЕХ! Профиль сгенерирован и сохранен в style_profile.json.")
        print("Твой стиль:", profile.get("tone", "неизвестно"))
        
    except Exception as e:
        print("❌ Ошибка при обращении к LLM:", e)

if __name__ == "__main__":
    asyncio.run(build_profile())